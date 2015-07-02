#!/usr/bin/env python

#
# Copyright 2015, Daehwan Kim <infphilo@gmail.com>
#
# This file is part of HISAT 2.
#
# HISAT 2 is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# HISAT 2 is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with HISAT 2.  If not, see <http://www.gnu.org/licenses/>.
#

import sys, math, random, re
from collections import defaultdict, Counter
from argparse import ArgumentParser, FileType


"""
"""
def reverse_complement(seq):
    result = ""
    for nt in seq:
        base = nt
        if nt == 'A':
            base = 'T'
        elif nt == 'a':
            base = 't'
        elif nt == 'C':
            base = 'G'
        elif nt == 'c':
            base = 'g'
        elif nt == 'G':
            base = 'C'
        elif nt == 'g':
            base = 'c'
        elif nt == 'T':
            base = 'A'
        elif nt == 't':
            base = 'a'
        
        result = base + result
    
    return result


"""
"""
def read_genome(genome_file):
    chr_dic = {}
    
    chr_name, sequence = "", ""
    for line in genome_file:
        if line[0] == ">":
            if chr_name and sequence:
                chr_dic[chr_name] = sequence
            
            chr_name = line[1:-1]
            sequence = ""
        else:
            sequence += line[:-1]

    if chr_name and sequence:
        chr_dic[chr_name] = sequence
    
    return chr_dic


"""
"""
def read_transcript(gtf_file, frag_len):
    genes = defaultdict(list)
    transcripts = {}

    # Parse valid exon lines from the GTF file into a dict by transcript_id
    for line in gtf_file:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '#' in line:
            line = line.split('#')[0].strip()
        try:
            chrom, source, feature, left, right, score, \
                strand, frame, values = line.split('\t')
        except ValueError:
            continue
        # Zero-based offset
        left, right = int(left) - 1, int(right) - 1
        if feature != 'exon' or left >= right:
            continue

        values_dict = {}
        for attr in values.split(';')[:-1]:
            attr, _, val = attr.strip().partition(' ')
            values_dict[attr] = val.strip('"')

        if 'gene_id' not in values_dict or \
                'transcript_id' not in values_dict:
            continue

        transcript_id = values_dict['transcript_id']
        if transcript_id not in transcripts:
            transcripts[transcript_id] = [chrom, strand, [[left, right]]]
            genes[values_dict['gene_id']].append(transcript_id)
        else:
            transcripts[transcript_id][2].append([left, right])

    # Sort exons and merge where separating introns are <=5 bps
    for tran, [chr, strand, exons] in transcripts.items():
            exons.sort()
            tmp_exons = [exons[0]]
            for i in range(1, len(exons)):
                if exons[i][0] - tmp_exons[-1][1] <= 5:
                    tmp_exons[-1][1] = exons[i][1]
                else:
                    tmp_exons.append(exons[i])
            transcripts[tran] = [chr, strand, tmp_exons]

    tmp_transcripts = {}
    for tran, [chr, strand, exons] in transcripts.items():
        exon_lens = [e[1] - e[0] + 1 for e in exons]
        transcript_len = sum(exon_lens)
        if transcript_len >= frag_len:
            tmp_transcripts[tran] = [chr, strand, transcript_len, exons]

    transcripts = tmp_transcripts

    return genes, transcripts
    

"""
"""
def read_snp(snp_file):
    snps = defaultdict(list)
    for line in snp_file:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            snpID, type, chr, pos, data = line.split('\t')
        except ValueError:
            continue

        assert type in ["single", "deletion", "insertion"]
        if type == "deletion":
            data = int(data)
        snps[chr].append([snpID, type, int(pos), data])

    return snps


"""
"""
def sanity_check_input(genome_seq, genes, transcripts, snps, frag_len):
    num_canon_ss, num_ss = 0, 0
    for transcript, [chr, strand, transcript_len, exons] in transcripts.items():
        assert transcript_len >= frag_len
        if len(exons) <= 1:
            continue
        if chr not in genome_seq:
            continue
        chr_seq = genome_seq[chr]
        for i in range(len(exons) - 1):
            left1, right1 = exons[i]
            assert left1 < right1
            left2, right2 = exons[i+1]
            assert left2 < right2
            assert left1 < left2 and right1 < right2
            donor = chr_seq[right1+1:right1+3]
            acceptor = chr_seq[left2-2:left2]
            if strand == "-":
                donor, acceptor = reverse_complement(acceptor), reverse_complement(donor)
            if donor == "GT" and acceptor == "AG":
                num_canon_ss += 1
            num_ss += 1

    print >> sys.stderr, "GT/AG splice sites: {}/{} ({:.2%})".format(num_canon_ss, num_ss, (float(num_canon_ss) / num_ss))

    num_alt_single, num_single = 0, 0
    for chr, chr_snps in snps.items():
        if chr not in genome_seq:
            continue
        chr_seq = genome_seq[chr]
        prev_snp = None
        for snp in chr_snps:
            snpID, type, pos, data = snp
            if prev_snp:
                assert prev_snp[2] <= pos
            prev_snp = snp
            if type != "single":
                continue
            assert pos < len(chr_seq)
            if chr_seq[pos] != data:
                num_alt_single += 1
            num_single += 1

    print >> sys.stderr, "Alternative bases: {}/{} ({:.2%})".format(num_alt_single, num_single, (float(num_alt_single) / num_single))


"""
"""
def generate_expr_profile(expr_profile_type, num_transcripts = 10000):
    # Modelling and simulating generic RNA-Seq experiments with the flux simulator
    # http://nar.oxfordjournals.org/content/suppl/2012/06/29/gks666.DC1/nar-02667-n-2011-File002.pdf
    def calc_expr(x, a):
        x, a, b = float(x), 9500.0, 9500.0
        k = -0.6
        return (x**k) * math.exp(x/a * (x/b)**2)
    
    expr_profile = [0.0] * num_transcripts
    for i in range(len(expr_profile)):
        if expr_profile_type == "flux":
            expr_profile[i] = calc_expr(i + 1, num_transcripts)
        elif expr_profile_type == "constant":
            expr_profile[i] = 1.0
        else:
            assert False

    expr_sum = sum(expr_profile)
    expr_profile = [expr_profile[i] / expr_sum for i in range(len(expr_profile))]
    assert abs(sum(expr_profile) - 1.0) < 0.001
    return expr_profile


"""
"""
def getSNPs(chr_snps, left, right):
    low, high = 0, len(chr_snps)
    while low < high:
        mid = (low + high) / 2
        snpID, type, pos, data = chr_snps[mid]
        if pos < left:
            low = mid + 1
        else:
            high = mid - 1

    snps = []
    for snp in chr_snps[low:]:
        snpID, type, pos, data = snp
        pos2 = pos
        if type == "deletion":
            pos2 += data
        if pos2 >= right:
            break
        if pos >= left:
            if len(snps) > 0:
                _, prev_type, prev_pos, prev_data = snps[-1]
                assert prev_pos <= pos
                prev_pos2 = prev_pos
                if prev_type == "deletion":
                    prev_pos2 += prev_data
                if pos <= prev_pos2:
                    continue
            snps.append(snp)

    return snps


"""
"""
def getSamAlignment(exons, trans_seq, frag_pos, read_len, chr_snps, error_rate):
    # Find the genomic position for frag_pos and exon number
    tmp_frag_pos, tmp_read_len = frag_pos, read_len
    pos, cigars, cigar_descs = exons[0][0], [], []
    e_pos = 0
    prev_e = None
    for e_i in range(len(exons)):
        e = exons[e_i]
        if prev_e:
            i_len = e[0] - prev_e[1] - 1
            pos += i_len
        e_len = e[1] - e[0] + 1
        if e_len <= tmp_frag_pos:
            tmp_frag_pos -= e_len
            pos += e_len
        else:
            pos += tmp_frag_pos
            e_pos = tmp_frag_pos
            break                        
        prev_e = e

    # Define Cigar and its descriptions
    assert e_i < len(exons)
    e_len = exons[e_i][1] - exons[e_i][0] + 1
    assert e_pos < e_len
    cur_pos = pos
    match_len = 0
    prev_e = None
    for e in exons[e_i:]:
        if prev_e:
            i_len = e[0] - prev_e[1] - 1
            cur_pos += i_len
            cigars.append(("{}N".format(i_len)))
            cigar_descs.append([])
        tmp_e_left = e_left = e[0] + e_pos
        e_pos = 0
        snps = getSNPs(chr_snps, e_left, e[1])
        cigar_descs.append([])
        prev_snp = None
        for snp in snps:
            snp_id, snp_type, snp_pos, snp_data = snp
            if prev_snp:
                prev_snp_id, prev_snp_type, prev_snp_pos, prev_snp_data = prev_snp
                if prev_snp_type == "deletion":
                    prev_snp_pos += prev_snp_data
                assert prev_snp_pos < snp_pos
            snp_pos2 = snp_pos
            if snp_type == "deletion":
                snp_pos2 += snp_data
            if e_left + tmp_read_len - 1 < snp_pos2 or e[1] < snp_pos2:
                break            
            if snp_type == "single":
                cigar_descs[-1].append([snp_pos - tmp_e_left, snp_data, snp_id])
                tmp_e_left = snp_pos + 1
            elif snp_type == "deletion":
                if len(cigars) > 0:
                    del_len = snp_data
                    if snp_pos - e_left > 0:
                        cigars.append("{}M".format(snp_pos - e_left))
                        cigar_descs[-1].append([snp_pos - tmp_e_left, "", ""])
                        cigar_descs.append([])
                    cigars.append("{}D".format(del_len))
                    cigar_descs[-1].append([0, del_len, snp_id])
                    cigar_descs.append([])
                    tmp_read_len -= (snp_pos - e_left)
                    e_left = tmp_e_left = snp_pos + del_len
            elif snp_type == "insertion":
                # To be implemented
                continue
                if len(cigars) > 0:
                    ins_len = len(snp_data)
                    if snp_pos - e_left > 0:
                        cigars.append("{}M".format(snp_pos - e_left))
                        cigar_descs[-1].append([snp_pos - tmp_e_left, "", ""])
                        cigar_descs.append([])
                    cigars.append("{}I".format(ins_len))
                    cigar_descs[-1].append([0, snp_data, snp_id])
                    cigar_descs.append([])
                    tmp_read_len -= (snp_pos - e_left)
                    tmp_read_len -= ins_len
                    e_left = tmp_e_left = snp_pos
            else:
                assert False
            prev_snp = snp

        e_right = min(e[1], e_left + tmp_read_len - 1)
        e_len = e_right - e_left + 1
        remain_e_len = e_right - tmp_e_left + 1
        if remain_e_len > 0:
            cigar_descs[-1].append([remain_e_len, "", ""])
        if e_len < tmp_read_len:
            tmp_read_len -= e_len
            cigars.append(("{}M".format(e_len)))
        else:
            assert e_len == tmp_read_len
            cigars.append(("{}M".format(tmp_read_len)))
            tmp_read_len = 0
            break
        prev_e = e

    # Define MD, XM, NM, Zs, read_seq
    MD, XM, NM, Zs, read_seq = "", 0, 0, "", ""
    assert len(cigars) == len(cigar_descs)
    match_len = 0
    cur_trans_pos = frag_pos
    for c in range(len(cigars)):
        cigar = cigars[c]
        cigar_len, cigar_op = int(cigar[:-1]), cigar[-1]
        cigar_desc = cigar_descs[c]
        if cigar_op == 'N':
            continue
        if cigar_op == 'M':
            for add_match_len, alt_base, snp_id in cigar_desc:
                match_len += add_match_len
                assert cur_trans_pos + add_match_len <= len(trans_seq)
                read_seq += trans_seq[cur_trans_pos:cur_trans_pos+add_match_len]
                cur_trans_pos += add_match_len
                if alt_base != "":
                    if match_len > 0:
                        MD += ("{}".format(match_len))
                    MD += trans_seq[cur_trans_pos]
                    if snp_id != "":
                        if Zs != "":
                            Zs += ","
                        Zs += ("{}|S|{}".format(match_len, snp_id))
                    match_len = 0
                    if snp_id == "":
                        XM += 1
                        NM += 1
                    read_seq += alt_base
                    cur_trans_pos += 1
        elif cigar_op == 'D':
            assert len(cigar_desc) == 1
            add_match_len, del_len, snp_id = cigar_desc[0]
            match_len += add_match_len
            if match_len > 0:
                MD += ("{}".format(match_len))
            MD += ("^{}".format(trans_seq[cur_trans_pos:cur_trans_pos+cigar_len]))
            read_seq += trans_seq[cur_trans_pos:cur_trans_pos+add_match_len]
            if Zs != "":
                Zs += ","
            Zs += ("{}|D|{}".format(match_len, cigar_desc[0][-1]))
            match_len = 0
            cur_trans_pos += cigar_len
        elif cigar_op == 'I':
            assert len(cigar_desc) == 1
            add_match_len, ins_len, snp_id = cigar_desc[0]
            match_len += add_match_len
            if match_len > 0:
                MD += ("{}".format(match_len))
            read_seq += trans_seq[cur_trans_pos:cur_trans_pos+add_match_len]
            if Zs != "":
                Zs += ","
            Zs += ("{}|I|{}".format(match_len, cigar_desc[0][-1]))
            match_len = 0
            read_pos += cigar_len
        else:
            assert False

    if match_len > 0:
        MD += ("{}".format(match_len))

    # daehwan - for debugging purposes
    if "I" in "".join(cigars) and False:
        print >> sys.stderr, pos, "".join(cigars), cigar_descs, MD, XM, NM, Zs, read_seq
        # sys.exit(1)

    if len(read_seq) != read_len:
        print >> sys.stderr, "read length differs:", len(read_seq), "vs.", read_len
        assert False

    return pos, cigars, cigar_descs, MD, XM, NM, Zs, read_seq


"""
"""
cigar_re = re.compile('\d+\w')
def samRepOk(genome_seq, read_seq, chr, pos, cigar, XM, NM, MD, Zs):
    assert chr in genome_seq
    chr_seq = genome_seq[chr]
    assert pos < len(chr_seq)

    # Calculate XM and NM based on Cigar and Zs
    cigars = cigar_re.findall(cigar)
    cigars = [[int(cigars[i][:-1]), cigars[i][-1]] for i in range(len(cigars))]
    ref_pos, read_pos = pos, 0
    ann_ref_seq, ann_ref_rel, ann_read_seq, ann_read_rel = [], [], [], []
    for i in range(len(cigars)):
        cigar_len, cigar_op = cigars[i]
        if cigar_op == "M":
            partial_ref_seq = chr_seq[ref_pos:ref_pos+cigar_len]
            partial_read_seq = read_seq[read_pos:read_pos+cigar_len]
            assert len(partial_ref_seq) == len(partial_read_seq)
            ann_ref_seq += list(partial_ref_seq)
            ann_read_seq += list(partial_read_seq)
            for j in range(len(partial_ref_seq)):
                if partial_ref_seq[j] == partial_read_seq[j]:
                    ann_ref_rel.append("=")
                    ann_read_rel.append("=")
                else:
                    ann_ref_rel.append("X")
                    ann_read_rel.append("X")
            ref_pos += cigar_len
            read_pos += cigar_len
        elif cigar_op == "D":
            partial_ref_seq = chr_seq[ref_pos:ref_pos+cigar_len]
            ann_ref_rel += list(partial_ref_seq)
            ann_ref_seq += list(partial_ref_seq)
            ann_read_rel += (["-"] * cigar_len)
            ann_read_seq += (["-"] * cigar_len)
            ref_pos += cigar_len
        elif cigar_op == "I":
            partial_read_seq = read_seq[read_pos:read_pos+cigar_len]
            ann_ref_rel += (["-"] * cigar_len)
            ann_ref_seq += (["-"] * cigar_len)
            ann_read_rel += list(read_seq)
            ann_read_seq += list(read_seq) 
            read_pos += cigar_len
        elif cigar_op == "N":
            ref_pos += cigar_len
        else:
            assert False        
    
    assert len(ann_ref_seq) == len(ann_read_seq)
    assert len(ann_ref_seq) == len(ann_ref_rel)
    assert len(ann_ref_seq) == len(ann_read_rel)
    ann_Zs_seq = ["0" for i in range(len(ann_ref_seq))]

    Zss, Zs_i, snp_pos_add = [], 0, 0
    if Zs != "":
        Zss = Zs.split(',')
        Zss = [zs.split('|') for zs in Zss]

    ann_read_pos = 0
    for zs in Zss:
        zs_pos, zs_type, zs_id = zs
        zs_pos = int(zs_pos)
        for i in range(zs_pos):
            while ann_read_rel[ann_read_pos] == '-':
                ann_read_pos += 1
            ann_read_pos += 1
        if zs_type == "S":
            ann_Zs_seq[ann_read_pos] = "1"
            ann_read_pos += 1
        elif zs_type == "D":
            while ann_read_rel[ann_read_pos] == '-':
                ann_Zs_seq[ann_read_pos] = "1"
                ann_read_pos += 1
        elif zs_type == "I":
            assert False
        else:
            assert False

    # daehwan - for debugging purposes
    if "D" in cigar and False:
        # print cigar
        # print Zss
        print len(ann_ref_seq), "".join(ann_ref_seq)
        print len(ann_ref_rel), "".join(ann_ref_rel)
        print len(ann_read_rel), "".join(ann_read_rel)
        print len(ann_Zs_seq), "".join(ann_Zs_seq)
        print len(ann_read_seq), "".join(ann_read_seq)
        sys.exit(1)


    tMD, tXM, tNM = "", 0, 0
    match_len = 0
    i = 0
    while i < len(ann_ref_seq):
        if ann_ref_rel[i] == "=":
            assert ann_read_rel[i] == "="
            match_len += 1
            i += 1
            continue
        assert ann_read_rel[i] != "="
        if ann_ref_rel[i] == "X" and ann_read_rel[i] == "X":
            if match_len > 0:
                tMD += ("{}".format(match_len))
                match_len = 0
            tMD += ann_ref_seq[i]
            if ann_Zs_seq[i] == "0":
                XM += 1
                NM += 1
            i += 1
        else:
            assert ann_ref_rel[i] == "-" or ann_read_rel[i] == "-"
            if ann_ref_rel[i] == '-':
                while ann_ref_rel[i] == '-':
                    if ann_Zs_seq[i] == "0":
                        NM += 1
                    i += 1
            else:
                assert ann_read_rel[i] == '-'
                del_seq = ""
                while  ann_read_rel[i] == '-':
                    del_seq += ann_ref_seq[i]
                    if ann_Zs_seq[i] == "0":
                        NM += 1
                    i += 1
                if match_len > 0:
                    tMD += ("{}".format(match_len))
                    match_len = 0
                tMD += ("^{}".format(del_seq))

    if match_len > 0:
        tMD += ("{}".format(match_len))

    if tMD != MD or tXM != XM or tNM != NM:
        print >> sys.stderr, chr, pos, cigar, MD, XM, NM, Zs
        print >> sys.stderr, tMD, tXM, tNM
        assert False
        
        
"""
"""
def simulate_reads(genome_file, gtf_file, snp_file, base_fname, \
                       rna, paired_end, read_len, frag_len, \
                       num_frag, expr_profile_type, error_rate, random_seed, \
                       sanity_check, verbose):
    random.seed(random_seed)
    if read_len > frag_len:
        frag_len = read_len

    genome_seq = read_genome(genome_file)
    genes, transcripts = read_transcript(gtf_file, frag_len)
    snps = read_snp(snp_file)

    if sanity_check:
        sanity_check_input(genome_seq, genes, transcripts, snps, frag_len)

    num_transcripts = min(len(transcripts), 10000)
    expr_profile = generate_expr_profile(expr_profile_type, num_transcripts)
    expr_profile = [int(expr_profile[i] * num_frag) for i in range(len(expr_profile))]

    assert num_frag >= sum(expr_profile)
    expr_profile[0] += (num_frag - sum(expr_profile))
    assert num_frag == sum(expr_profile)

    transcript_ids = transcripts.keys()
    random.shuffle(transcript_ids)

    sam_file = open(base_fname + ".sam", "w")
    read_file = open(base_fname + "_1.fa", "w")
    if paired_end:
        read2_file = open(base_fname + "_2.fa", "w")

    assert len(transcript_ids) >= len(expr_profile)
    cur_read_id = 1
    for t in range(len(expr_profile)):
        transcript_id = transcript_ids[t]
        chr, strand, transcript_len, exons = transcripts[transcript_id]

        # daehwan - for debugging purposes
        # if transcript_id != "ENST00000354373":
        #    continue
        print >> sys.stderr, transcript_id
        
        t_num_frags = expr_profile[t]
        t_seq = ""
        assert chr in genome_seq
        chr_seq = genome_seq[chr]
        for e in exons:
            assert e[0] < e[1]
            t_seq += chr_seq[e[0]:e[1]+1]

        assert len(t_seq) == transcript_len
        for f in range(t_num_frags):
            frag_pos = random.randint(0, transcript_len - frag_len)
            assert frag_pos + frag_len <= transcript_len

            if chr in snps:
                chr_snps = snps[chr]
            else:
                chr_snps = []

            # SAM specification (v1.4)
            # http://samtools.sourceforge.net/
            flag, flag2 = 99, 163  # 83, 147
            pos, cigars, cigar_descs, MD, XM, NM, Zs, read_seq = getSamAlignment(exons, t_seq, frag_pos, read_len, chr_snps, error_rate)
            pos2, cigars2, cigar2_descs, MD2, XM2, NM2, Zs2, read2_seq = getSamAlignment(exons, t_seq, frag_pos+frag_len-read_len, read_len, chr_snps, error_rate)
            swapped = False
            if paired_end:
                if random.randint(0, 1) == 1:
                    swapped = True
                if swapped:
                    flag, flag2 = flag2 - 16, flag - 16
                    pos, pos2 = pos2, pos
                    cigars, cigars2 = cigars2, cigars
                    cigar_descs, cigar2_descs = cigar2_descs, cigar_descs
                    read_seq, read2_seq = read2_seq, read_seq
                    XM, XM2 = XM2, XM
                    NM, NM2 = NM2, NM
                    MD, MD2 = MD2, MD
                    Zs, Zs2 = Zs2, Zs

            cigar_str, cigar2_str = "".join(cigars), "".join(cigars2)
            if sanity_check:
                samRepOk(genome_seq, read_seq, chr, pos, cigar_str, XM, NM, MD, Zs)
                samRepOk(genome_seq, read2_seq, chr, pos2, cigar2_str, XM2, NM2, MD2, Zs2)

            if Zs != "":
                Zs = ("\tZs:Z:{}".format(Zs))
            if Zs2 != "":
                Zs2 = ("\tZs:Z:{}".format(Zs2))

            print >> read_file, ">{}".format(cur_read_id)
            if swapped:
                print >> read_file, reverse_complement(read_seq)
            else:
                print >> read_file, read_seq
            print >> sam_file, "{}\t{}\t{}\t{}\t255\t{}\t{}\t{}\t0\t{}\t*\tXM:i:{}\tNM:i:{}\tMD:Z:{}{}\tTI:Z:{}".format(cur_read_id, flag, chr, pos, cigar_str, chr, pos2, read_seq, XM, NM, MD, Zs, transcript_id)
            if paired_end:
                print >> read2_file, ">{}".format(cur_read_id)
                if swapped:
                    print >> read2_file, read2_seq
                else:
                    print >> read2_file, reverse_complement(read2_seq)
                print >> sam_file, "{}\t{}\t{}\t{}\t255\t{}\t{}\t{}\t0\t{}\t*\tXM:i:{}\tNM:i:{}\tMD:Z:{}{}\tTI:Z:{}".format(cur_read_id, flag2, chr, pos2, cigar2_str, chr, pos, read2_seq, XM2, NM2, MD2, Zs2, transcript_id)

            cur_read_id += 1
            
    sam_file.close()
    read_file.close()
    if paired_end:
        read2_file.close()


if __name__ == '__main__':
    parser = ArgumentParser(
        description='Simulate reads from GENOME (fasta) and GTF files')
    parser.add_argument('genome_file',
                        nargs='?',
                        type=FileType('r'),
                        help='input GENOME file')
    parser.add_argument('gtf_file',
                        nargs='?',
                        type=FileType('r'),
                        help='input GTF file')
    parser.add_argument('snp_file',
                        nargs='?',
                        type=FileType('r'),
                        help='input SNP file')
    parser.add_argument('base_fname',
                        nargs='?',
                        type=str,
                        help='output base filename')
    parser.add_argument('-d', '--dna',
                        dest='rna',
                        action='store_false',
                        default=True,
                        help='DNA-seq reads (default: RNA-seq reads)')
    parser.add_argument('--single-end',
                        dest='paired_end',
                        action='store_false',
                        default=True,
                        help='single-end reads (default: paired-end reads)')
    parser.add_argument('-r', '--read-length',
                        dest='read_len',
                        action='store',
                        type=int,
                        default=100,
                        help='read length (default: 100)')
    parser.add_argument('-f', '--fragment-length',
                        dest='frag_len',
                        action='store',
                        type=int,
                        default=250,
                        help='fragment length (default: 250)')
    parser.add_argument('-n', '--num-fragment',
                        dest='num_frag',
                        action='store',
                        type=int,
                        default=1000000,
                        help='number of fragments (default: 1000000)')
    parser.add_argument('-e', '--expr-profile',
                        dest='expr_profile',
                        action='store',
                        type=str,
                        default='flux',
                        help='expression profile: flux or constant (default: flux)')
    parser.add_argument('--error-rate',
                        dest='error_rate',
                        action='store',
                        type=float,
                        default=0.0,
                        help='per-base sequencing error rate (default: 0.0)')
    parser.add_argument('--random-seed',
                        dest='random_seed',
                        action='store',
                        type=int,
                        default=0,
                        help='random seeding value (default: 0)')
    parser.add_argument('--sanity-check',
                        dest='sanity_check',
                        action='store_true',
                        help='sanity check')
    parser.add_argument('-v', '--verbose',
                        dest='verbose',
                        action='store_true',
                        help='also print some statistics to stderr')
    parser.add_argument('--version', 
                        action='version',
                        version='%(prog)s 2.0.0-alpha')
    args = parser.parse_args()
    if not args.genome_file or not args.gtf_file or not args.snp_file:
        parser.print_help()
        exit(1)
    simulate_reads(args.genome_file, args.gtf_file, args.snp_file, args.base_fname, \
                       args.rna, args.paired_end, args.read_len, args.frag_len, \
                       args.num_frag, args.expr_profile, args.error_rate, args.random_seed, \
                       args.sanity_check, args.verbose)
