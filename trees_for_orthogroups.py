# -*- coding: utf-8 -*-
"""
Created on Thu Sep 25 13:15:22 2014

@author: david
"""
import os
import sys
import multiprocessing as mp
import time
import subprocess
import Queue
import glob

import orthofinder    

version = "0.2.8"
nProcessesDefault = 16
    

def RunCommandSet(commandSet):
    orthofinder.util.PrintTime("Runing command: %s" % commandSet[-1])
    for cmd in commandSet:
        subprocess.call(cmd, shell=True)
    orthofinder.util.PrintTime("Finshed command: %s" % commandSet[-1])
    
class FastaWriter(object):
    def __init__(self, fastaFileDir):
        self.SeqLists = dict()
        qFirst = True
        accession = ""
        sequence = ""
        for fn in glob.glob(fastaFileDir + "Species*.fa"):
            with open(fn, 'rb') as fastaFile:
                for line in fastaFile:
                    if line[0] == ">":
                        # deal with old sequence
                        if not qFirst:
                            self.SeqLists[accession] = sequence
                            sequence = ""
                        qFirst = False
                        # get id for new sequence
                        accession = line[1:].rstrip()
                    else:
                        sequence += line
                self.SeqLists[accession] = sequence
    
    def WriteSeqsToFasta(self, seqs, outFilename):
        with open(outFilename, 'wb') as outFile:
            for seq in seqs:
                if seq in self.SeqLists:
                    outFile.write(">%s\n" % seq)
                    outFile.write(self.SeqLists[seq])
                else:
                    print("ERROR: %s not found" % seq)
                                
    def WriteSeqsToFasta_withNewAccessions(self, seqs, outFilename, idDict):
        with open(outFilename, 'wb') as outFile:
            for seq in seqs:
                if seq in self.SeqLists:
                    outFile.write(">%s\n" % idDict[seq])
                    outFile.write(self.SeqLists[seq])
                    
                    
def Worker_RunCommand(cmd_queue):
    """ repeatedly takes items to process from the queue until it is empty at which point it returns. Does not take a new task
        if it can't acquire queueLock as this indicates the queue is being rearranged.
        
        Writes each commands output and stderr to a file
    """
    while True:
        try:
            commandSet = cmd_queue.get(True, 10)
            RunCommandSet(commandSet)
        except Queue.Empty:
            return   
    
def RunParallelCommandSets(nProcesses, commands):
    
    # Setup the workers and run
    cmd_queue = mp.Queue()
    for cmd in commands:
        cmd_queue.put(cmd)
    runningProcesses = [mp.Process(target=Worker_RunCommand, args=(cmd_queue,)) for i_ in xrange(nProcesses)]
    for proc in runningProcesses:
        proc.start()
    
    for proc in runningProcesses:
        while proc.is_alive():
            proc.join(60.)
            time.sleep(2)    

def WriteTestFile(workingDir):
    testFN = workingDir + "SimpleTest.fa"
    with open(testFN, 'wb') as outfile:
        outfile.write(">a\nA\n>b\nA")
    return testFN

def IsWorkingDirectory(orthofinderWorkingDir):
    ok = True
    ok = ok and len(glob.glob(orthofinderWorkingDir + "clusters_OrthoFinder_*.txt_id_pairs.txt")) > 0
    ok = ok and len(glob.glob(orthofinderWorkingDir + "Species*.fa")) > 0
    return ok
      
class TreesForOrthogroups(object):
    def __init__(self, baseOutputDir, orthofinderWorkingDir):
        self.baseOgFormat = "OG%07d"
        self.baseOutputDir = baseOutputDir
        self.orthofinderWorkingDir = orthofinderWorkingDir
    
    def Align_linsi(self, fasta, alignedFasta, alignmentReport, nThreads=1):
        return "mafft-linsi --anysymbol --thread %d %s > %s 2> %s" % (nThreads, fasta, alignedFasta, alignmentReport)   
        
    def Align_mafft(self, fasta, alignedFasta, alignmentReport, nThreads=1):
        """ For larger numbers of sequences (>500 perhaps)"""
        return "mafft --anysymbol --thread %d %s > %s 2> %s" % (nThreads, fasta, alignedFasta, alignmentReport)   
    
    def GetFastaFilename(self, iOG):
        return self.baseOutputDir + "Sequences/" + (self.baseOgFormat % iOG) + ".fa"
    def GetAlignmentFilename(self, iOG):
        return self.baseOutputDir + "Alignments/" + (self.baseOgFormat % iOG) + ".fa"
    def GetTreeFilename(self, iOG):
        return self.baseOutputDir + "Trees/" + (self.baseOgFormat % iOG) + "_tree.txt"
        
    def WriteFastaFiles(self, fastaWriter, ogs, idDict):
        for iOg, og in enumerate(ogs):
            filename = self.GetFastaFilename(iOg)
            fastaWriter.WriteSeqsToFasta_withNewAccessions(og, filename, idDict)
      
    def OGsStillToDo(self, ogs):
        retOGs = []
        nDone = 0
        for i, og in enumerate(ogs):
            treeFilename = self.GetTreeFilename(i)
            if os.path.isfile(treeFilename) and os.path.getsize(treeFilename) != 0:
                nDone +=1
                pass
            elif len(og) > 1:
                retOGs.append((i, og))
        return retOGs, nDone
              
    def GetAlignmentCommands(self, IandOGs_toDo, nSwitchToMafft, nThreads=1):
        commands = []
        for i, og in IandOGs_toDo:
            ogFastaFilename = self.GetFastaFilename(i)
            alignedFilename = self.GetAlignmentFilename(i)
            reportFilename = "/dev/null"
            if len(og) < nSwitchToMafft:
                commands.append(self.Align_linsi(ogFastaFilename, alignedFilename, reportFilename, nThreads=nThreads))
            else:
                commands.append(self.Align_mafft(ogFastaFilename, alignedFilename, reportFilename, nThreads=nThreads))
        return commands
        
    def GetTreeCommands(self, alignmenstsForTree, IandOGs_toDo):
        commands = []
        for (i, og), alignFN in zip(IandOGs_toDo, alignmenstsForTree):
            treeFilename = self.GetTreeFilename(i)
            commands.append("FastTree %s > %s 2> /dev/null" % (alignFN, treeFilename))
        return commands
               
    def DoTrees(self, ogs, idDict, nProcesses, nSwitchToMafft=500):
        
        testFN = WriteTestFile(self.orthofinderWorkingDir)
        if not orthofinder.CanRunCommand("mafft %s" % testFN, qAllowStderr=True):
            print("ERROR: Cannot run mafft")
            print("Please check MAFFT is installed and that the executables are in the system path\n")
            return False
        if not orthofinder.CanRunCommand("mafft-linsi %s" % testFN, qAllowStderr=True):
            print("ERROR: Cannot run mafft-linsi")
            print("Please check mafft-linsi is installed and that the executables are in the system path\n")
            return False
        if not orthofinder.CanRunCommand("FastTree %s" % testFN, qAllowStderr=True):
            print("ERROR: Cannot run FastTree")
            print("Please check FastTree is installed and that the executables are in the system path\n")
            return False
        os.remove(testFN)
        
        # 0
        dirs = ['Sequences', 'Alignments', 'Trees']
        for d in dirs:
            if not os.path.exists(self.baseOutputDir + d):
                os.mkdir(self.baseOutputDir + d)
        
        # 1.
        fastaWriter = FastaWriter(self.orthofinderWorkingDir)
        self.WriteFastaFiles(fastaWriter, ogs, idDict)
        print("\nFasta files for orthogroups have been written to:\n   %s" % self.baseOutputDir + "Sequences/")
        
        # 2
        IandOGs_toDo, nDone = self.OGsStillToDo(ogs)
        if nDone != 0: print("\nAlignments and trees have already been generated for %d orthogroups" % nDone)
        print("\nAlignments and trees will be generated for %d orthogroups" % len(IandOGs_toDo)) 
        
        # 3
        alignCommands = self.GetAlignmentCommands(IandOGs_toDo, nSwitchToMafft)
        alignmentFilesToUse = [self.GetAlignmentFilename(i) for i, og in IandOGs_toDo]
        treeCommands = self.GetTreeCommands(alignmentFilesToUse, IandOGs_toDo)
        commandsSet = [(alignCmd, treeCms) for alignCmd, treeCms in zip(alignCommands, treeCommands)]
            
        # 4
        if len(commandsSet) > 0:
            print("\nExample commands that will be run:")
            for cmdSet in commandsSet[:10]:
                for cmd in cmdSet:
                    print(cmd)
            print("")
            
        RunParallelCommandSets(nProcesses, commandsSet)
        
        orthofinder.PrintCitation()
        print("\nFasta files for orthogroups have been written to:\n   %s\n" % (self.baseOutputDir + "Sequences/"))
        print("Multiple sequences alignments have been written to:\n   %s\n" % (self.baseOutputDir + "Alignments/"))
        print("Gene trees have been written to:\n   %s\n" % (self.baseOutputDir + "Trees/"))
 
def PrintHelp():
    print("Usage:\n")    
    print("python AlignmentsAndTrees.py orthofinder_results_directory [-t max_number_of_threads]")
    print("python AlignmentsAndTrees.py -h")
    print("\n")
    
    print("Arguments:\n")
    print("""orthofinder_results_directory
   Generate multiple sequence alignments and trees for the orthogroups in orthofinder_results_directory.\n""")
    
    print("""-t max_number_of_threads, --threads max_number_of_threads
   The maximum number of processes to be run simultaneously. The deafult is %d but this 
   should be increased by the user to the maximum number of cores available.\n""" % nProcessesDefault)
        
    print("""-h, --help
   Print this help text\n""")
    orthofinder.PrintCitation()   

def GetIDsDict(orthofinderWorkingDir):
    # sequence IDs
    idExtract = orthofinder.FirstWordExtractor(orthofinderWorkingDir + "SequenceIDs.txt")
    idDict = idExtract.GetIDToNameDict()
    
    # species names
    speciesDict = dict()
    with open(orthofinderWorkingDir + "SpeciesIDs.txt", 'rb') as idsFile:
        for line in idsFile:
            iSpecies, filename = line.rstrip().split(": ", 1)
            speciesName = os.path.splitext(os.path.split(filename)[1])[0]
            speciesDict[iSpecies] = speciesName   
    idDict = {seqID:speciesDict[seqID.split("_")[0]] + "_" + name for seqID, name in idDict.items()}
    return idDict    

if __name__ == "__main__":
    print("\nOrthoFinder Alignments and Trees version %s Copyright (C) 2015 David Emms\n" % version)
    print("""    This program comes with ABSOLUTELY NO WARRANTY.
    This is free software, and you are welcome to redistribute it under certain conditions.
    For details please see the License.md that came with this software.\n""")
    if len(sys.argv) == 1 or sys.argv[1] == "--help" or sys.argv[1] == "help" or sys.argv[1] == "-h":
        PrintHelp()
        sys.exit()
        
    v = map(int, orthofinder.version.split("."))
    v = 100 * v[0] + 10*v[1] + v[2] 
    if v < 28: 
        print("ERROR: OrthoFinder program has not been updated, please update 'orthofinder.py' to the version %s\n" % version)
        orthofinder.Fail()

    # Get arguments    
    orthofinderResultsDir = None
    nProcesses = None
    
    args = sys.argv[1:]    
    while len(args) != 0:
        arg = args.pop(0)
        if arg == "-t" or arg == "--threads":
            if len(args) == 0:
                print("Missing option for command line argument -t")
                orthofinder.Fail()
            arg = args.pop(0)
            try:
                nProcesses = int(arg)
            except:
                print("Incorrect argument for number of threads: %s" % arg)
                orthofinder.Fail()   
        else:
            orthofinderResultsDir = arg
    
    # Check arguments
    if orthofinderResultsDir == None:
        print("ERROR: orthofinder_results_directory has not been specified")
        orthofinder.Fail()
    if orthofinderResultsDir[-1] != os.path.sep: orthofinderResultsDir += os.path.sep
    
    orthogroupsFile = orthofinderResultsDir + "OrthologousGroups.txt"
    print("Generating trees for orthogroups in file:\n   %s\n" % orthogroupsFile)
    if not os.path.exists(orthogroupsFile):
        print("ERROR: Orthogroups file was not found in orthofinder_results_directory. Could not find:\n   %s\n" % orthogroupsFile)
        orthofinder.Fail()

    if nProcesses == None:
        print("""Number of parallel processes has not been specified, will use the default value.  
   Number of parallel processes can be specified using the -t option\n""")
        nProcesses = nProcessesDefault
    print("Using %d threads for alignments and trees\n" % nProcesses)
    
    orthofinderWorkingDir = orthofinderResultsDir
    if not IsWorkingDirectory(orthofinderWorkingDir):
        orthofinderWorkingDir = orthofinderResultsDir + "WorkingDirectory" + os.sep   
        if not IsWorkingDirectory(orthofinderWorkingDir):
            print("ERROR: cannot find files from OrthoFinder run in either:\n   %s\nor\n   %s\n" % (orthofinderResultsDir, orthofinderWorkingDir))
    
    files = glob.glob(orthofinderWorkingDir + "clusters_OrthoFinder_*.txt_id_pairs.txt")
    clustersFilename_pairs = files[0]
    ogs = orthofinder.MCL.GetPredictedOGs(clustersFilename_pairs)     
    idDict = GetIDsDict(orthofinderWorkingDir)
    
    treeGen = TreesForOrthogroups(orthofinderResultsDir, orthofinderWorkingDir)
    treeGen.DoTrees(ogs, idDict, nProcesses, nSwitchToMafft=500)

