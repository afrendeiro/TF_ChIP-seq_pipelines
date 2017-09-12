#!/usr/bin/env python

"""
Quant-seq pipeline
"""

from argparse import ArgumentParser
import os
import sys
from . import toolkit as tk
import cPickle as pickle
from pypiper import Pypiper


__author__ = "Andre Rendeiro"
__copyright__ = "Copyright 2015, Andre Rendeiro"
__credits__ = []
__license__ = "GPL2"
__version__ = "0.1"
__maintainer__ = "Andre Rendeiro"
__email__ = "arendeiro@cemm.oeaw.ac.at"
__status__ = "Development"


def main():
    # Parse command-line arguments
    parser = ArgumentParser(description="Quant-seq pipeline.")
    parser = mainArgParser(parser)
    args = parser.parse_args()
    # save pickle
    samplePickle = args.samplePickle

    # Read in objects
    prj, sample, args = pickle.load(open(args.samplePickle, "rb"))

    # Start main function
    process(args, prj, sample)

    # Remove pickle
    if not args.dry_run:
        os.system("rm %s" % samplePickle)

    # Exit
    print("Finished and exiting.")

    sys.exit(0)


def mainArgParser(parser):
    """
    Global options for pipeline.
    """
    # Project
    parser.add_argument(
        dest="samplePickle",
        help="Pickle with tuple of: (pipelines.Project, pipelines.Sample, argparse.ArgumentParser).",
        type=str
    )
    return parser


def process(args, prj, sample):
    """
    This takes unmapped Bam files and merges them if needed, assesses raw read quality,
    trims reads, aligns, marks and removes duplicates and indexes files.
    Transcript quantifications follows.
    """
    print("Start processing Quant-seq sample %s." % sample.name)

    # Start Pypiper object
    pipe = Pypiper("pipe", sample.dirs.sampleRoot, args=args)

    # if more than one technical replicate, merge bams
    if type(sample.unmappedBam) == list:
        pipe.timestamp("Merging bam files from replicates")
        cmd = tk.mergeBams(
            inputBams=sample.unmappedBam,  # this is a list of sample paths
            outputBam=sample.unmapped
        )
        pipe.call_lock(cmd, sample.unmapped, shell=True)
        sample.unmappedBam = sample.unmapped

    # Fastqc
    pipe.timestamp("Measuring sample quality with Fastqc")
    cmd = tk.fastqc(
        inputBam=sample.unmappedBam,
        outputDir=sample.dirs.sampleRoot,
        sampleName=sample.name
    )
    pipe.call_lock(cmd, os.path.join(sample.dirs.sampleRoot, sample.name + "_fastqc.zip"), shell=True)

    # Convert bam to fastq
    pipe.timestamp("Converting to Fastq format")
    cmd = tk.bam2fastq(
        inputBam=sample.unmappedBam,
        outputFastq=sample.fastq1 if sample.paired else sample.fastq,
        outputFastq2=sample.fastq2 if sample.paired else None,
        unpairedFastq=sample.fastqUnpaired if sample.paired else None
    )
    pipe.call_lock(cmd, sample.fastq1 if sample.paired else sample.fastq, shell=True)
    if not sample.paired:
        pipe.clean_add(sample.fastq, conditional=True)
    if sample.paired:
        pipe.clean_add(sample.fastq1, conditional=True)
        pipe.clean_add(sample.fastq2, conditional=True)
        pipe.clean_add(sample.fastqUnpaired, conditional=True)

    # Trim reads
    pipe.timestamp("Trimming adapters from sample")
    # Use of trimmomatic is enforced in this pipeline regardless of args.trimmer
    cmd = trimmomatic(
        inputFastq1=sample.fastq1 if sample.paired else sample.fastq,
        inputFastq2=sample.fastq2 if sample.paired else None,
        outputFastq1=sample.trimmed1 if sample.paired else sample.trimmed,
        outputFastq1unpaired=sample.trimmed1Unpaired if sample.paired else None,
        outputFastq2=sample.trimmed2 if sample.paired else None,
        outputFastq2unpaired=sample.trimmed2Unpaired if sample.paired else None,
        cpus=args.cpus,
        adapters=prj.config["adapters"],
        log=sample.trimlog
    )
    pipe.call_lock(cmd, sample.trimmed1 if sample.paired else sample.trimmed, shell=True)
    if not sample.paired:
        pipe.clean_add(sample.trimmed, conditional=True)
    else:
        pipe.clean_add(sample.trimmed1, conditional=True)
        pipe.clean_add(sample.trimmed1Unpaired, conditional=True)
        pipe.clean_add(sample.trimmed2, conditional=True)
        pipe.clean_add(sample.trimmed2Unpaired, conditional=True)

    # Map
    pipe.timestamp("Mapping sample with Tophat")
    cmd = tk.topHatMap(
        inputFastq=sample.trimmed1 if sample.paired else sample.trimmed,
        outDir=sample.dirs.mapped,
        genome=prj.config["annotations"]["genomes"][sample.genome],
        transcriptome=prj.config["annotations"]["transcriptomes"][sample.genome],
        cpus=args.cpus
    )
    pipe.call_lock(cmd, sample.mapped, shell=True)
    pipe.clean_add(sample.mapped, conditional=True)

    pipe.timestamp("Mapping erccs with Bowtie2")
    cmd = tk.bowtie2Map(
        inputFastq1=sample.trimmed1 if sample.paired else sample.trimmed,
        inputFastq2=sample.trimmed1 if sample.paired else None,
        outputBam=sample.erccMapped,
        log=sample.erccAlnRates,
        metrics=sample.erccAlnMetrics,
        genomeIndex=prj.config["annotations"]["genomes"]["ercc"],
        maxInsert=args.maxinsert,
        cpus=args.cpus
    )
    pipe.call_lock(cmd, sample.erccMapped, shell=True)
    pipe.clean_add(sample.erccMapped, conditional=True)

    # Filter reads
    pipe.timestamp("Filtering reads")
    cmd = tk.filterReads(
        inputBam=sample.mapped,
        outputBam=sample.filtered,
        metricsFile=sample.dupsMetrics,
        paired=sample.paired,
        cpus=args.cpus,
        Q=args.quality
    )
    pipe.call_lock(cmd, sample.filtered, shell=True)

    pipe.timestamp("Filtering ERCC reads")
    cmd = tk.filterReads(
        inputBam=sample.erccMapped,
        outputBam=sample.erccFiltered,
        metricsFile=sample.erccDupsMetrics,
        paired=sample.paired,
        cpus=args.cpus,
        Q=args.quality
    )
    pipe.call_lock(cmd, sample.erccFiltered, shell=True)

    # Sort and index
    pipe.timestamp("Sorting and indexing reads")
    cmd = tk.sortIndexBam(
        inputBam=sample.filtered,
        outputBam=sample.filtered
    )
    pipe.call_lock(cmd, lock_name="sample.filtered", shell=True)

    pipe.timestamp("Sorting and indexing ERCC reads")
    cmd = tk.sortIndexBam(
        inputBam=sample.erccFiltered,
        outputBam=sample.erccFiltered
    )
    pipe.call_lock(cmd, lock_name="sample.erccFiltered", shell=True)

    # Quantify Transcripts
    # With HTseq-count from alignments
    pipe.timestamp("Quantify sample transcripts with htseq-count")
    cmd = tk.htSeqCount(
        inputBam=sample.filtered,
        gtf=prj.config["annotations"]["transcriptomes"][sample.genome],
        output=sample.quant
    )
    pipe.call_lock(cmd, sample.quant, shell=True)

    pipe.timestamp("Quantify ERCC transcripts with htseq-count")
    cmd = tk.htSeqCount(
        inputBam=sample.erccFiltered,
        gtf=prj.config["annotations"]["transcriptomes"]["ercc"],
        output=sample.erccQuant
    )
    pipe.call_lock(cmd, sample.erccQuant, shell=True, nofail=True)

    # With kallisto from unmapped reads
    pipe.timestamp("Quantifying read counts with kallisto")
    cmd = tk.kallisto(
        inputFastq=sample.trimmed1 if sample.paired else sample.trimmed,
        inputFastq2=sample.trimmed2 if sample.paired else None,
        outputDir=sample.dirs.quant,
        outputBam=sample.pseudomapped,
        transcriptomeIndex=prj.config["annotations"]["kallistoindex"][sample.genome],
        cpus=args.cpus
    )
    pipe.call_lock(cmd, sample.kallistoQuant, shell=True, nofail=True)

    pipe.stop_pipeline()
    print("Finished processing sample %s." % sample.name)


def trimmomatic(inputFastq1, outputFastq1, cpus, adapters, log,
                inputFastq2=None, outputFastq1unpaired=None,
                outputFastq2=None, outputFastq2unpaired=None):

    PE = False if inputFastq2 is None else True
    pe = "PE" if PE else "SE"

    cmd = "java -Xmx4g -jar `which trimmomatic-0.32.jar`"
    cmd += " {0} -threads {1} -trimlog {2} {3}".format(pe, cpus, log, inputFastq1)
    if PE:
        cmd += " {0}".format(inputFastq2)
    cmd += " {0}".format(outputFastq1)
    if PE:
        cmd += " {0} {1} {2}".format(outputFastq1unpaired, outputFastq2, outputFastq2unpaired)
    cmd += " ILLUMINACLIP:{0}:1:40:15:8:true".format(adapters)
    cmd += " HEADCROP:12"
    cmd += " TRAILING:3"
    cmd += " SLIDINGWINDOW:4:10"
    cmd += " MINLEN:36"

    return cmd


if __name__ == '__main__':
    try:
        main()
        sys.exit(0)
    except KeyboardInterrupt:
        print("Program canceled by user!")
        sys.exit(1)
