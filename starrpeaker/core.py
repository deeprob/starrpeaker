#!/usr/bin/python
from __future__ import division

__author__ = "Donghoon Lee"
__copyright__ = "Copyright 2019, Gerstein Lab"
__credits__ = ["Donghoon Lee"]
__license__ = "GPL"
__version__ = "1.0.0"
__maintainer__ = "Donghoon Lee"
__email__ = "donghoon.lee@yale.edu"

import numpy as np
import pandas as pd
import pybedtools
import pyBigWig
import pysam
from scipy.stats import nbinom
from scipy.special import digamma, polygamma
import statsmodels.formula.api as smf
import statsmodels.api as sm
import statsmodels.stats.multitest as multi
import os, uuid, datetime
from itertools import compress


def timestamp():
    return str(datetime.datetime.now()).split('.')[0]


def safe_remove(file):
    if os.path.exists(file):
        os.remove(file)


def get_uid():
    return str(uuid.uuid4())[:8]


def make_bin(chromSize, binLength, stepSize, blackList, fileOut):
    ### make sliding window
    print("[%s] Making bins" % (timestamp()))
    bin = pybedtools.BedTool().window_maker(g=chromSize, w=binLength, s=stepSize)

    ### filter blacklist region
    print("[%s] Filtering blacklist region" % (timestamp()))
    blk = pybedtools.BedTool(blackList).sort()
    out = bin.intersect(blk, v=True, sorted=True)

    ### write to file
    with open(fileOut, 'w') as file:
        file.write(str(out))
    del bin, blk, out
    print("[%s] Done" % (timestamp()))


def proc_cov(bwFiles, bedFile, fileOut):
    ### average bigwig over bin bed
    print("[%s] Averaging features per bin" % (timestamp()))
    mat = np.zeros(shape=(sum(1 for l in open(bedFile)), len(bwFiles)), dtype=float)
    for j, bw in enumerate(bwFiles):
        print("[%s] Processing %s" % (timestamp(), bw))
        b = pyBigWig.open(bw)
        with open(bedFile, "r") as bed:
            for i, bin in enumerate(bed.readlines()):
                chr, start, end = bin.strip().split("\t")
                val = b.stats(chr, int(start), int(end), type="mean")
                if isinstance(val[0], float):
                    mat[i][j] = val[0]
        b.close()
    np.savetxt(fileOut, mat, fmt='%.2f', delimiter="\t")
    print("[%s] Done" % (timestamp()))
    del mat


def list_chr(chromSize):
    with open(chromSize, "r") as cs:
        return [c.split("\t")[0] for c in cs]


def count_total_mapped_reads(bam):
    idxstats_by_line = [l.split("\t") for l in pysam.idxstats(bam).split("\n")]
    idxstats_by_line_clean = filter(lambda x: len(x) == 4, idxstats_by_line)
    return reduce(lambda x, y: x + y, [int(count_by_chr[2]) for count_by_chr in idxstats_by_line_clean])


def count_total_proper_templates(bam, minSize, maxSize):
    if not os.path.exists(bam + ".bai"):
        print("[%s] (Warning) Index not found: %s" % (timestamp(), bam))
        print("[%s] Indexing %s" % (timestamp(), bam))
        pysam.index(bam)
    b = pysam.AlignmentFile(bam, "rb")

    proper_pair_count = 0
    chimeric_count = 0
    template_count = 0
    proper_template_count = 0

    for read in b.fetch():
        ### read is in proper pair
        ### read is NOT chimeric read (i.e., no SA tag)
        ### read is mapped to forward strand, mate is mapped to reverse strand
        if read.is_proper_pair:
            proper_pair_count += 1
            if read.has_tag("SA"):
                chimeric_count += 1
            else:
                if not read.is_reverse and read.mate_is_reverse:
                    template_count += 1
                    if read.template_length >= int(minSize) and read.template_length <= int(maxSize):
                        proper_template_count += 1
    b.close()
    return proper_template_count


def proc_bam(bamFiles, bedFile, chromSize, fileOut, minSize, maxSize, normalize=False, pseudocount=1):
    '''

    Args:
        bamFiles: list of BAM files eg. [input.bam output.bam]
        bedFile: bin BED file
        chromSize: chrom size file
        fileOut: output file
        minSize: minimum size of fragment insert to consider
        maxSize: maximum size of fragment insert to consider
        normalize: if True, normalized input count is added to additional column
        pseudocount: pseudocount for input normalization

    Returns:
        writes bin count output file

    '''
    print("[%s] Counting template depth per bin %s" % (timestamp(), bedFile))

    ### initialize numpy array
    tct = np.zeros(shape=(len(bamFiles)), dtype=int)

    ### initialize numpy array
    mat = np.zeros(shape=(sum(1 for l in open(bedFile)), len(bamFiles)), dtype=int)

    ### random unique ID
    uid = get_uid()

    ### load bin bed file
    a = pybedtools.BedTool(bedFile)

    for j, bam in enumerate(bamFiles):
        print("[%s] Processing %s" % (timestamp(), bam))

        if not os.path.exists(bam + ".bai"):
            print("[%s] (Warning) Index not found: %s" % (timestamp(), bam))
            print("[%s] Indexing %s" % (timestamp(), bam))
            pysam.index(bam)

        b = pysam.AlignmentFile(bam, "rb")

        proper_pair_count = 0
        chimeric_count = 0
        template_count = 0
        proper_template_count = 0

        for chr in list_chr(chromSize):

            print("[%s] Processing %s" % (timestamp(), chr))

            with open("tmp" + uid + str(j) + chr + ".bed", "w") as s:
                for read in b.fetch(reference=chr):

                    ### read is in proper pair
                    ### read is NOT chimeric read (i.e., no SA tag)
                    ### read is mapped to forward strand, mate is mapped to reverse strand
                    if read.is_proper_pair:
                        proper_pair_count += 1
                        if read.has_tag("SA"):
                            chimeric_count += 1
                        else:
                            if not read.is_reverse and read.mate_is_reverse:
                                template_count += 1
                                if read.template_length >= int(minSize) and read.template_length <= int(maxSize):
                                    proper_template_count += 1
                                    s.write("%s\t%i\t%i\n" % ((b.get_reference_name(read.reference_id)),
                                                              (read.reference_start + int(read.template_length / 2)), (
                                                                  read.reference_start + int(
                                                                      read.template_length / 2) + 1)))

            print("[%s] Sorting %s" % (timestamp(), chr))
            pybedtools.BedTool("tmp" + uid + str(j) + chr + ".bed").sort().saveas(
                "tmp" + uid + str(j) + chr + "sorted.bed")

            ### delete bed
            safe_remove("tmp" + uid + str(j) + chr + ".bed")

        tct[j] += proper_template_count

        print("[%s] Total mapped reads: %i" % (timestamp(), b.mapped))
        print("[%s] %i reads in proper pairs" % (timestamp(), proper_pair_count))
        print("[%s] %i chimeric reads removed" % (timestamp(), chimeric_count))
        print("[%s] %i templates extracted" % (timestamp(), template_count))
        print("[%s] %i templates used for count" % (timestamp(), proper_template_count))

        b.close()

        ### merge bed
        print("[%s] Merging BED files" % (timestamp()))
        with open("tmp" + uid + str(j) + ".merged.bed", "a") as merged:
            for chr in list_chr(chromSize):

                ### merge tmp bed files
                with open("tmp" + uid + str(j) + chr + "sorted.bed", "r") as t:
                    if t.read(1).strip():
                        t.seek(0)
                        merged.write(t.read())

                ### delete tmp bed files
                safe_remove("tmp" + uid + str(j) + chr + "sorted.bed")

        print("[%s] Counting depth per bin" % (timestamp()))
        mergedBed = pybedtools.BedTool("tmp" + uid + str(j) + ".merged.bed")
        readDepth = a.coverage(mergedBed, sorted=True, counts=True)

        ### extract 4th column, which is read counts, and assign as numpy array
        mat[:, j] = np.array([int(l.split("\t")[3]) for l in str(readDepth).rstrip("\n").split("\n")])

        ### save genome coverage
        mergedBed.genome_coverage(bg=True, g=chromSize).saveas(fileOut + "." + str(j) + ".bdg")

        ### delete tmp merged bed files
        safe_remove("tmp" + uid + str(j) + ".merged.bed")
        del merged, readDepth

    if normalize:
        ### normalize input count
        normalized_input = mat[:, 0] * (tct[1] / tct[0])
        nonzero = normalized_input != 0
        normalized_input[nonzero] += float(pseudocount)
        np.savetxt(fileOut, np.concatenate((mat, normalized_input.reshape(-1, 1)), axis=1), fmt='%.5f', delimiter="\t")
    else:
        np.savetxt(fileOut, mat, fmt='%i', delimiter="\t")

    print("[%s] Done" % (timestamp()))
    del a, mat, tct


def proc_bam_readstart(bamFiles, bedFile, chromSize, fileOut, normalize=False, pseudocount=1):
    print("[%s] Counting template depth per bin %s" % (timestamp(), bedFile))

    ### initialize numpy array
    tct = np.zeros(shape=(len(bamFiles)), dtype=int)

    ### initialize numpy array
    mat = np.zeros(shape=(sum(1 for l in open(bedFile)), len(bamFiles)), dtype=int)

    ### random unique ID
    uid = get_uid()

    ### load bin bed file
    a = pybedtools.BedTool(bedFile)

    for j, bam in enumerate(bamFiles):
        print("[%s] Processing %s" % (timestamp(), bam))

        if not os.path.exists(bam + ".bai"):
            print("[%s] (Warning) Index not found: %s" % (timestamp(), bam))
            print("[%s] Indexing %s" % (timestamp(), bam))
            pysam.index(bam)

        b = pysam.AlignmentFile(bam, "rb")

        proper_pair_count = 0
        chimeric_count = 0
        template_count = 0
        proper_template_count = 0

        for chr in list_chr(chromSize):

            print("[%s] Processing %s" % (timestamp(), chr))

            with open("tmp" + uid + str(j) + chr + ".bed", "w") as s:
                for read in b.fetch(reference=chr):
                    s.write("%s\t%i\t%i\n" % ((b.get_reference_name(read.reference_id)), (read.reference_start),
                                              (read.reference_start + 1)))  ## start position of read

            print("[%s] Sorting %s" % (timestamp(), chr))
            pybedtools.BedTool("tmp" + uid + str(j) + chr + ".bed").sort().saveas(
                "tmp" + uid + str(j) + chr + "sorted.bed")

            ### delete bed
            safe_remove("tmp" + uid + str(j) + chr + ".bed")

        tct[j] += proper_template_count

        print("[%s] Total mapped reads: %i" % (timestamp(), b.mapped))
        print("[%s] %i reads in proper pairs" % (timestamp(), proper_pair_count))
        print("[%s] %i chimeric reads removed" % (timestamp(), chimeric_count))
        print("[%s] %i templates extracted" % (timestamp(), template_count))
        print("[%s] %i templates used for count" % (timestamp(), proper_template_count))

        b.close()

        ### merge bed
        print("[%s] Merging BED files" % (timestamp()))
        with open("tmp" + uid + str(j) + ".merged.bed", "a") as merged:
            for chr in list_chr(chromSize):

                ### merge tmp bed files
                with open("tmp" + uid + str(j) + chr + "sorted.bed", "r") as t:
                    if t.read(1).strip():
                        t.seek(0)
                        merged.write(t.read())

                ### delete tmp bed files
                safe_remove("tmp" + uid + str(j) + chr + "sorted.bed")

        print("[%s] Counting depth per bin" % (timestamp()))
        readDepth = a.coverage(pybedtools.BedTool("tmp" + uid + str(j) + ".merged.bed"), sorted=True, counts=True)

        ### extract 4th column, which is read counts, and assign as numpy array
        mat[:, j] = np.array([int(l.split("\t")[3]) for l in str(readDepth).rstrip("\n").split("\n")])

        ### delete tmp merged bed files
        # safe_remove("tmp" + uid + str(j) + ".merged.bed")
        os.rename("tmp" + uid + str(j) + ".merged.bed", fileOut + "." + str(j) + ".bdg")

    if normalize:
        ### normalize input count
        normalized_input = mat[:, 0] * (tct[1] / tct[0])
        nonzero = normalized_input != 0
        normalized_input[nonzero] += float(pseudocount)
        np.savetxt(fileOut, np.concatenate((mat, normalized_input.reshape(-1, 1)), axis=1), fmt='%.5f', delimiter="\t")
    else:
        np.savetxt(fileOut, mat, fmt='%i', delimiter="\t")

    print("[%s] Done" % (timestamp()))
    del a, mat, tct


def trigamma(x):
    return polygamma(1, x)


def score(th, mu, y, w):
    return sum(w * (digamma(th + y) - digamma(th) + np.log(th) + 1 - np.log(th + mu) - (y + th) / (mu + th)))


def info(th, mu, y, w):
    return sum(w * (-trigamma(th + y) + trigamma(th) - 1 / th + 2 / (mu + th) - (y + th) / (mu + th) ** 2))


def theta(y, mu, verbose=False):
    ### MLE for theta and std. error

    ### stop iteration if delta smaller than eps
    eps = np.finfo(np.float).eps ** 0.25

    ### max iter
    limit = 20

    ### init
    weights = np.repeat(1, len(y))
    n = sum(weights)
    t0 = n / sum(weights * (y / mu - 1) ** 2)
    it = 0
    de = 1

    if (verbose): print("theta: iter %d theta = %f" % (it, t0))

    while (it < limit and abs(de) > eps):
        it += 1
        t0 = abs(t0)
        de = score(t0, mu, y, weights) / info(t0, mu, y, weights)
        t0 = t0 + de
        if (verbose): print("theta: iter %d theta = %f" % (it, t0))

    ### warning
    if (t0 < 0):
        t0 = 0
        print("warning: estimate truncated at zero")
    if (it == limit):
        print("warning: iteration limit reached")

    ### standard error
    se = np.sqrt(1 / info(t0, mu, y, weights))

    return t0, se


def call_peak(prefix, bedFile, bctFile, covFile, threshold, minInputQuantile=0):
    print("[%s] Calling peaks" % (timestamp()))

    ### load data
    print("[%s] Loading response, exposure, and covariates" % (timestamp()))
    bct = np.loadtxt(bctFile, ndmin=2)  # input, output, normalized input
    cov = np.loadtxt(covFile, ndmin=2)

    ### merge data
    mat = np.concatenate((bct[:, 1:], cov), axis=1)  # output, normalized input
    del bct, cov

    ### remove bins with normalized input count of zero (i.e., untested region) OR below "minimum threshold" defined by minInputQuantile
    minInput = np.quantile(mat[(mat[:, 1] > 0), 1], float(minInputQuantile))
    print("[%s] Minimum Normalized Input Coverage: %f" % (timestamp(), minInput))
    nonZeroInput = mat[:, 1] > minInput

    ### non sliding bins
    nonSliding = np.zeros(mat.shape[0], dtype=bool)  ### initialize with False
    with open(bedFile, "r") as bed:
        lastchr, lastbin = "", 0
        for i, bin in enumerate(bed.readlines()):
            if bin.split("\t")[0] != lastchr:
                lastchr = bin.split("\t")[0]
                lastbin = int(bin.split("\t")[2])
                nonSliding[i] = True
            elif int(bin.split("\t")[1]) >= lastbin:
                lastbin = int(bin.split("\t")[2])
                nonSliding[i] = True

    ### filter inputs with zero
    print("[%s] Removing %i bins with insufficient input coverage" % (timestamp(), sum(np.invert(nonZeroInput))))
    print("[%s] Removing %i sliding bins" % (timestamp(), sum(np.invert(nonSliding))))

    print("[%s] Before filtering: %s" % (timestamp(), mat.shape))
    print("[%s] Bins with sufficient input coverage: %s" % (timestamp(), mat[nonZeroInput, :].shape))
    print("[%s] Non sliding bin: %s" % (timestamp(), mat[nonSliding, :].shape))
    print("[%s] After filtering:: %s" % (timestamp(), mat[nonZeroInput & nonSliding, :].shape))

    ### formula
    x = ["x" + str(i) for i in range(1, mat.shape[1] - 1)]
    df = pd.DataFrame(mat[nonZeroInput & nonSliding, :], columns=["y", "exposure"] + x)
    formula = "y~" + "+".join(df.columns.difference(["y", "exposure"]))
    print("[%s] Fit using formula: %s" % (timestamp(), formula))

    ### Initial parameter estimation using Poisson regression
    print("[%s] Initial estimate" % (timestamp()))
    model0 = smf.glm(formula, data=df, family=sm.families.Poisson(), offset=np.log(df["exposure"])).fit()
    # print model0.summary()

    ### Estimate theta
    th0, _ = theta(mat[nonZeroInput & nonSliding, :][:, 0], model0.mu)
    print("[%s] Initial estimate of theta is %f" % (timestamp(), th0))

    ### re-estimate beta with theta
    print("[%s] Re-estimate of beta" % (timestamp()))
    model = smf.glm(formula, data=df, family=sm.families.NegativeBinomial(alpha=1 / th0),
                    offset=np.log(df["exposure"])).fit(start_params=model0.params)
    # print model.summary()

    ### Re-estimate theta
    th, _ = theta(mat[nonZeroInput & nonSliding, :][:, 0], model.mu)
    print("[%s] Re-estimate of theta is %f" % (timestamp(), th))

    ### predict
    df = pd.DataFrame(mat[nonZeroInput, :], columns=["y", "exposure"] + x)
    y_hat = model.predict(df, offset=np.log(df["exposure"]))

    ### calculate P-value
    print("[%s] Calculating P-value" % (timestamp()))
    theta_hat = np.repeat(th, len(y_hat))
    prob = th / (th + y_hat)  ### prob=theta/(theta+mu)
    pval = 1 - nbinom.cdf(mat[nonZeroInput, 0] - 1, n=theta_hat, p=prob)
    del mat

    ### multiple testing correction
    print("[%s] Multiple testing correction" % (timestamp()))
    _, pval_adj, _, _ = multi.multipletests(pval, method="fdr_bh")

    p_score = -np.log10(pval)
    q_score = -np.log10(pval_adj)

    ### output peak
    with open(prefix + ".peak.bed", "w") as out:
        with open(bedFile, "r") as bed:
            for i, bin in enumerate(list(compress(bed.readlines(), nonZeroInput))):
                if pval_adj[i] <= float(threshold):
                    out.write("%s\t%.3f\t%.3f\t%.5e\t%.5e\n" % (
                        bin.strip(), p_score[i], q_score[i], pval[i], pval_adj[i]))
    del p_score, q_score

    ### output p-val track
    print("[%s] Generating P-value track" % (timestamp()))
    with open(prefix + ".pval.bedGraph", "w") as out:
        with open(bedFile, "r") as bed:
            for i, bin in enumerate(list(compress(bed.readlines(), nonZeroInput))):
                out.write("%s\t%.3f\n" % (bin.strip(), abs(p_score[i])))
    del pval, pval_adj

    ### merge peak
    print("[%s] Merge peaks" % (timestamp()))
    pybedtools.BedTool(prefix + ".peak.bed").merge(c=[4, 5, 6, 7], o=["max", "max", "min", "min"]).saveas(
        prefix + ".peak.merged.bed")

    ### center merged peak
    print("[%s] Finalizing peaks" % (timestamp()))
    center_peak(prefix + ".peak.merged.bed", bctFile + ".1.bdg", prefix + ".peak.final.bed")

    print("[%s] Done" % (timestamp()))


def make_pval_bigwig(prefix, bedGraphFile, chromsize):
    print("[%s] Making P-value BigWig Tracks" % (timestamp()))
    with open(chromsize) as f: cs = [line.strip().split('\t') for line in f.readlines()]

    bin = np.genfromtxt(bedGraphFile, dtype=str)

    starts = np.array(bin[:, 1], dtype=np.int64)
    ends = np.array(bin[:, 2], dtype=np.int64)

    l = ends[0] - starts[0]
    s = starts[1] - starts[0]
    print("[%s] Using fixed interval of %i" % (timestamp(), s))

    nonoverlapping = ends - starts == l

    chroms = (np.array(bin[:, 0]))[nonoverlapping]
    starts = (np.array(bin[:, 1], dtype=np.int64) + int(l / 2) - int(s / 2))[nonoverlapping]
    ends = (np.array(bin[:, 2], dtype=np.int64) - int(l / 2) + int(s / 2))[nonoverlapping]
    val_pval = np.array(bin[:, 3], dtype=np.float64)[nonoverlapping]

    ### pval signal

    bw = pyBigWig.open(prefix + ".pval.bw", "w")
    bw.addHeader([(str(x[0]), int(x[1])) for x in cs])
    bw.addEntries(chroms=chroms, starts=starts, ends=ends, values=val_pval)
    bw.close()

    print("[%s] Done" % (timestamp()))


def make_bigwig(prefix, bedFile, bctFile, chromsize, bedGraphFile=""):
    print("[%s] Making BigWig Tracks" % (timestamp()))
    with open(chromsize) as f: cs = [line.strip().split('\t') for line in f.readlines()]

    bin = np.genfromtxt(bedFile, dtype=str)
    bct = np.loadtxt(bctFile, dtype=np.float64, ndmin=2)

    starts = np.array(bin[:, 1], dtype=np.int64)
    ends = np.array(bin[:, 2], dtype=np.int64)

    l = ends[0] - starts[0]
    s = starts[1] - starts[0]
    print("[%s] Using fixed interval of %i" % (timestamp(), s))

    nonoverlapping = ends - starts == l

    chroms = (np.array(bin[:, 0]))[nonoverlapping]
    starts = (np.array(bin[:, 1], dtype=np.int64) + int(l / 2) - int(s / 2))[nonoverlapping]
    ends = (np.array(bin[:, 2], dtype=np.int64) - int(l / 2) + int(s / 2))[nonoverlapping]
    val_input = np.array(bct[:, 0], dtype=np.float64)[nonoverlapping]
    val_output = np.array(bct[:, 1], dtype=np.float64)[nonoverlapping]
    val_normalized_input = np.array(bct[:, 2], dtype=np.float64)[nonoverlapping]

    chroms_fc = chroms[np.nonzero(val_normalized_input)]
    starts_fc = starts[np.nonzero(val_normalized_input)]
    ends_fc = ends[np.nonzero(val_normalized_input)]
    val_fc = val_output[np.nonzero(val_normalized_input)] / val_normalized_input[np.nonzero(val_normalized_input)]

    ### input signal

    bw0 = pyBigWig.open(prefix + ".input.bw", "w")
    bw0.addHeader([(str(x[0]), int(x[1])) for x in cs])
    bw0.addEntries(chroms=chroms, starts=starts, ends=ends, values=val_input)
    bw0.close()

    ### output signal

    bw1 = pyBigWig.open(prefix + ".output.bw", "w")
    bw1.addHeader([(str(x[0]), int(x[1])) for x in cs])
    bw1.addEntries(chroms=chroms, starts=starts, ends=ends, values=val_output)
    bw1.close()

    ### normalized input signal

    bw2 = pyBigWig.open(prefix + ".normalized_input.bw", "w")
    bw2.addHeader([(str(x[0]), int(x[1])) for x in cs])
    bw2.addEntries(chroms=chroms, starts=starts, ends=ends, values=val_normalized_input)
    bw2.close()

    ### fold change

    bw3 = pyBigWig.open(prefix + ".fc.bw", "w")
    bw3.addHeader([(str(x[0]), int(x[1])) for x in cs])
    bw3.addEntries(chroms=chroms_fc, starts=starts_fc, ends=ends_fc, values=val_fc)
    bw3.close()

    # with open(prefix+".bedGraph","w") as b:
    #     b.write("track type=bedGraph\n")
    #     for x in zip(chroms,starts,ends,val_input):
    #         b.write('\t'.join(map(str,x))+'\n')

    print("[%s] Done" % (timestamp()))

    if bedGraphFile != "":
        make_pval_bigwig(prefix, bedGraphFile, chromsize)


def center_peak(peakFile, coverageFile, centeredPeakFile, windowSize=500):
    peak = pybedtools.BedTool(peakFile)
    peak_coverage = peak.intersect(pybedtools.BedTool(coverageFile), wa=True, wb=True)
    coverage = {}
    for pc in peak_coverage:
        pid = pc[0] + "_" + pc[1] + "_" + pc[2]
        if pid in coverage:
            coverage[pid].append([int(pc[8]), int(pc[9]), int(pc[10])])
        else:
            coverage[pid] = [[int(pc[8]), int(pc[9]), int(pc[10])]]
    with open(centeredPeakFile, "w") as out:
        for p in peak:
            pid = p[0] + "_" + p[1] + "_" + p[2]
            chr = p[0]
            start = int(p[1])
            end = int(p[2])
            other = '\t'.join(p[3:])
            cov = np.array(coverage[pid])
            # cov = np.array([[int(x[8]),int(x[9]),int(x[10])] for x in peak_coverage if x[0]+"_"+x[1]+"_"+x[2] == p[0]+"_"+p[1]+"_"+p[2]])
            if len(cov) == 0:
                print("[%s] Warning! No Intersect Found for peak: %s" % (timestamp(), p.strip()))
            else:
                mat = np.zeros(end - start, dtype=int)
                for c in cov:
                    mat[c[0] - start:c[1] - start] = c[2]
                depth = np.zeros(end - start - windowSize + 1, dtype=int)
                for idx in range(0, end - start - windowSize + 1):
                    depth[idx] = sum(mat[idx:idx + windowSize])
                peakstarts = np.argwhere(depth == np.max(depth))
                out.write('\t'.join(
                    [chr, str(peakstarts[0, 0] + start), str(peakstarts[-1, 0] + start + windowSize), other]) + '\n')
