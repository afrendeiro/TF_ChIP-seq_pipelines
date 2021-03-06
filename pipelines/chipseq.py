#!/usr/bin/env python

"""
ChIP-seq pipeline
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
    parser = ArgumentParser(
        prog="chipseq-pipeline",
        description="ChIP-seq pipeline."
    )
    parser = mainArgParser(parser)
    args = parser.parse_args()
    # save pickle
    samplePickle = args.samplePickle

    # Read in objects
    prj, sample, args = pickle.load(open(samplePickle, "rb"))

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
    This takes unmapped Bam files and makes trimmed, aligned, duplicate marked
    and removed, indexed (and shifted if necessary) Bam files
    along with a UCSC browser track.
    """
    print("Start processing ChIP-seq sample %s." % sample.name)

    # Start Pypiper object
    pipe = Pypiper("pipe", sample.dirs.sampleRoot, args=args)

    # Merge Bam files if more than one technical replicate
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
    if args.trimmer == "trimmomatic":
        cmd = tk.trimmomatic(
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

    elif args.trimmer == "skewer":
        cmd = tk.skewer(
            inputFastq1=sample.fastq1 if sample.paired else sample.fastq,
            inputFastq2=sample.fastq2 if sample.paired else None,
            outputPrefix=os.path.join(sample.dirs.unmapped, sample.name),
            outputFastq1=sample.trimmed1 if sample.paired else sample.trimmed,
            outputFastq2=sample.trimmed2 if sample.paired else None,
            trimLog=sample.trimlog,
            cpus=args.cpus,
            adapters=prj.config["adapters"]
        )
        pipe.call_lock(cmd, sample.trimmed1 if sample.paired else sample.trimmed, shell=True)
        if not sample.paired:
            pipe.clean_add(sample.trimmed, conditional=True)
        else:
            pipe.clean_add(sample.trimmed1, conditional=True)
            pipe.clean_add(sample.trimmed2, conditional=True)

    # Map
    pipe.timestamp("Mapping reads with Bowtie2")
    cmd = tk.bowtie2Map(
        inputFastq1=sample.trimmed1 if sample.paired else sample.trimmed,
        inputFastq2=sample.trimmed2 if sample.paired else None,
        outputBam=sample.mapped,
        log=sample.alnRates,
        metrics=sample.alnMetrics,
        genomeIndex=prj.config["annotations"]["genomes"][sample.genome],
        maxInsert=args.maxinsert,
        cpus=args.cpus
    )
    pipe.call_lock(cmd, sample.mapped, shell=True)
    pipe.clean_add(sample.mapped, conditional=True)

    # Filter reads
    pipe.timestamp("Filtering reads for quality")
    cmd = tk.filterReads(
        inputBam=sample.mapped,
        outputBam=sample.filtered,
        metricsFile=sample.dupsMetrics,
        paired=sample.paired,
        cpus=args.cpus,
        Q=args.quality
    )
    pipe.call_lock(cmd, sample.filtered, shell=True)

    # Shift reads
    if sample.tagmented:
        pipe.timestamp("Shifting reads of tagmented sample")
        cmd = tk.shiftReads(
            inputBam=sample.filtered,
            genome=sample.genome,
            outputBam=sample.filteredshifted
        )
        pipe.call_lock(cmd, sample.filteredshifted, shell=True)

    # Index bams
    pipe.timestamp("Indexing bamfiles with samtools")
    cmd = tk.indexBam(inputBam=sample.mapped)
    pipe.call_lock(cmd, sample.mapped + ".bai", shell=True)
    cmd = tk.indexBam(inputBam=sample.filtered)
    pipe.call_lock(cmd, sample.filtered + ".bai", shell=True)
    if sample.tagmented:
        cmd = tk.indexBam(inputBam=sample.filteredshifted)
        pipe.call_lock(cmd, sample.filteredshifted + ".bai", shell=True)

    # Make tracks
    # right now tracks are only made for bams without duplicates
    pipe.timestamp("Making bigWig tracks from bam file")
    cmd = tk.bamToBigWig(
        inputBam=sample.filtered,
        outputBigWig=sample.bigwig,
        genomeSizes=prj.config["annotations"]["chrsizes"][sample.genome],
        genome=sample.genome,
        tagmented=False,  # by default tracks are made for full extended reads
        normalize=True
    )
    pipe.call_lock(cmd, sample.bigwig, shell=True)
    cmd = tk.addTrackToHub(
        sampleName=sample.name,
        trackURL=sample.trackURL,
        trackHub=os.path.join(prj.dirs.html, "trackHub_{0}.txt".format(sample.genome)),
        colour=sample.trackColour
    )
    pipe.call_lock(cmd, lock_name=sample.name + "addToTrackHub", shell=True)
    tk.linkToTrackHub(
        trackHubURL="/".join([prj.config["url"], prj.name, "trackHub_{0}.txt".format(sample.genome)]),
        fileName=os.path.join(prj.dirs.root, "ucsc_tracks_{0}.html".format(sample.genome)),
        genome=sample.genome
    )

    # Count coverage genome-wide
    pipe.timestamp("Calculating genome-wide coverage")
    cmd = tk.genomeWideCoverage(
        inputBam=sample.filtered,
        genomeWindows=prj.config["annotations"]["genomewindows"][sample.genome],
        output=sample.coverage
    )
    pipe.call_lock(cmd, sample.coverage, shell=True)

    # Calculate NSC, RSC
    pipe.timestamp("Assessing signal/noise in sample")
    cmd = tk.peakTools(
        inputBam=sample.filtered,
        output=sample.qc,
        plot=sample.qcPlot,
        cpus=args.cpus
    )
    pipe.call_lock(cmd, sample.qcPlot, shell=True, nofail=True)

    # If sample does not have "ctrl" attribute, finish processing it.
    if not hasattr(sample, "ctrl"):
        print("Finished processing sample %s." % sample.name)
        return

    if args.peak_caller == "macs2":
        pipe.timestamp("Calling peaks with MACS2")
        # make dir for output (macs fails if it does not exist)
        if not os.path.exists(sample.dirs.peaks):
            os.makedirs(sample.dirs.peaks)

        # For point-source factors use default settings
        # For broad factors use broad settings
        cmd = tk.macs2CallPeaks(
            treatmentBam=sample.filtered,
            controlBam=sample.ctrl.filtered,
            outputDir=sample.dirs.peaks,
            sampleName=sample.name,
            genome=sample.genome,
            broad=True if sample.broad else False
        )
        pipe.call_lock(cmd, sample.peaks, shell=True)

        pipe.timestamp("Ploting MACS2 model")
        cmd = tk.macs2PlotModel(
            sampleName=sample.name,
            outputDir=os.path.join(sample.dirs.peaks, sample.name)
        )
        pipe.call_lock(cmd, os.path.join(sample.dirs.peaks, sample.name, sample.name + "_model.pdf"), shell=True)
    elif args.peak_caller == "spp":
        pipe.timestamp("Calling peaks with spp")
        # For point-source factors use default settings
        # For broad factors use broad settings
        cmd = tk.sppCallPeaks(
            treatmentBam=sample.filtered,
            controlBam=sample.ctrl.filtered,
            treatmentName=sample.name,
            controlName=sample.ctrl.sampleName,
            outputDir=os.path.join(sample.dirs.peaks, sample.name),
            broad=True if sample.broad else False,
            cpus=args.cpus
        )
        pipe.call_lock(cmd, sample.peaks, shell=True)
    elif args.peak_caller == "zinba":
        raise NotImplementedError("Calling peaks with Zinba is not yet implemented.")
        # pipe.timestamp("Calling peaks with Zinba")
        # cmd = tk.bamToBed(
        #     inputBam=sample.filtered,
        #     outputBed=os.path.join(sample.dirs.peaks, sample.name + ".bed"),
        # )
        # pipe.call_lock(cmd, os.path.join(sample.dirs.peaks, sample.name + ".bed"), shell=True)
        # cmd = tk.bamToBed(
        #     inputBam=sample.ctrl.filtered,
        #     outputBed=os.path.join(sample.dirs.peaks, control.sampleName + ".bed"),
        # )
        # pipe.call_lock(cmd, os.path.join(sample.dirs.peaks, control.sampleName + ".bed"), shell=True)
        # cmd = tk.zinbaCallPeaks(
        #     treatmentBed=os.path.join(sample.dirs.peaks, sample.name + ".bed"),
        #     controlBed=os.path.join(sample.dirs.peaks, control.sampleName + ".bed"),
        #     tagmented=sample.tagmented,
        #     cpus=args.cpus
        # )
        # pipe.call_lock(cmd, shell=True)

    # Find motifs
    pipe.timestamp("Finding motifs")
    if not sample.histone:
        # For TFs, find the "self" motif
        cmd = tk.homerFindMotifs(
            peakFile=sample.peaks,
            genome=sample.genome,
            outputDir=sample.motifsDir,
            size="50",
            length="8,10,12,14,16",
            n_motifs=8
        )
        pipe.call_lock(cmd, os.path.join(sample.motifsDir, "homerResults", "motif1.motif"), shell=True)
        # For TFs, find co-binding motifs (broader region)
        cmd = tk.homerFindMotifs(
            peakFile=sample.peaks,
            genome=sample.genome,
            outputDir=sample.motifsDir + "_cobinders",
            size="200",
            length="8,10,12,14,16",
            n_motifs=12
        )
        pipe.call_lock(cmd, os.path.join(sample.motifsDir + "_cobinders", "homerResults", "motif1.motif"), shell=True)
    else:
        # For histones, use a broader region to find motifs
        cmd = tk.homerFindMotifs(
            peakFile=sample.peaks,
            genome=sample.genome,
            outputDir=sample.motifsDir,
            size="1000",
            length="8,10,12,14,16",
            n_motifs=20
        )
        pipe.call_lock(cmd, os.path.join(sample.motifsDir, "homerResults", "motif1.motif"), shell=True)

    # Center peaks on motifs
    pipe.timestamp("Centering peak in motifs")
    # TODO:
    # right now this assumes peaks were called with MACS2
    # figure a way of magetting the peak files withough using the peak_caller option
    # for that would imply taht option would be required when selecting this stage
    cmd = tk.centerPeaksOnMotifs(
        peakFile=sample.peaks,
        genome=sample.genome,
        windowWidth=prj.config["options"]["peakwindowwidth"],
        motifFile=os.path.join(sample.motifsDir, "homerResults", "motif1.motif"),
        outputBed=sample.peaksMotifCentered
    )
    pipe.call_lock(cmd, sample.peaksMotifCentered, shell=True)

    # Annotate peaks with motif info
    pipe.timestamp("Annotating peaks with motif info")
    # TODO:
    # right now this assumes peaks were called with MACS2
    # figure a way of getting the peak files withough using the peak_caller option
    # for that would imply taht option would be required when selecting this stage
    cmd = tk.AnnotatePeaks(
        peakFile=sample.peaks,
        genome=sample.genome,
        motifFile=os.path.join(sample.motifsDir, "homerResults", "motif1.motif"),
        outputBed=sample.peaksMotifAnnotated
    )
    pipe.call_lock(cmd, sample.peaksMotifAnnotated, shell=True)

    # Plot enrichment at peaks centered on motifs
    pipe.timestamp("Ploting enrichment at peaks centered on motifs")
    cmd = tk.peakAnalysis(
        inputBam=sample.filtered,
        peakFile=sample.peaksMotifCentered,
        plotsDir=os.path.join(prj.dirs.results, 'plots'),
        windowWidth=prj.config["options"]["peakwindowwidth"],
        fragmentsize=1 if sample.tagmented else sample.readLength,
        genome=sample.genome,
        n_clusters=5,
        strand_specific=True,
        duplicates=True
    )
    pipe.call_lock(cmd, shell=True, nofail=True)

    # Plot enrichment around TSSs
    pipe.timestamp("Ploting enrichment around TSSs")
    cmd = tk.tssAnalysis(
        inputBam=sample.filtered,
        tssFile=prj.config["annotations"]["tss"][sample.genome],
        plotsDir=os.path.join(prj.dirs.results, 'plots'),
        windowWidth=prj.config["options"]["peakwindowwidth"],
        fragmentsize=1 if sample.tagmented else sample.readLength,
        genome=sample.genome,
        n_clusters=5,
        strand_specific=True,
        duplicates=True
    )
    pipe.call_lock(cmd, shell=True, nofail=True)

    # Calculate fraction of reads in peaks (FRiP)
    pipe.timestamp("Calculating fraction of reads in peaks (FRiP)")
    cmd = tk.calculateFRiP(
        inputBam=sample.filtered,
        inputBed=sample.peaks,
        output=sample.frip
    )
    pipe.call_lock(cmd, sample.frip, shell=True)

    pipe.stop_pipeline()
    print("Finished processing sample %s." % sample.name)


if __name__ == '__main__':
    try:
        main()
        sys.exit(0)
    except KeyboardInterrupt:
        print("Program canceled by user!")
        sys.exit(1)
