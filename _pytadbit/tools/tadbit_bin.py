"""

information needed

 - path working directory with parsed reads

"""
from argparse                        import HelpFormatter
from os                              import path, remove, system
from sys                             import stdout
from shutil                          import copyfile
from string                          import ascii_letters
from random                          import random
from cPickle                         import load
from warnings                        import warn
from multiprocessing                 import cpu_count
from collections                     import OrderedDict
import time

import sqlite3 as lite
from numpy                           import log2, zeros_like, ma, min as np_min
from numpy                           import nonzero, array, linspace
from matplotlib                      import pyplot as plt
from pysam                           import AlignmentFile

from pytadbit.mapping.filter         import MASKED
from pytadbit.utils.file_handling    import mkdir
from pytadbit.parsers.hic_bam_parser import filters_to_bin, printime
from pytadbit.parsers.hic_bam_parser import write_matrix, get_matrix
from pytadbit.utils.sqlite_utils     import already_run, digest_parameters
from pytadbit.utils.sqlite_utils     import add_path, get_jobid, print_db
from pytadbit.utils.extraviews       import tadbit_savefig, nicer


DESC = 'bin Hi-C data into matrices'


def run(opts):
    check_options(opts)
    launch_time = time.localtime()
    param_hash = digest_parameters(opts)

    if opts.zrange:
        vmin = float(opts.zrange.split(',')[0])
        vmax = float(opts.zrange.split(',')[1])
    else:
        vmin = vmax = None

    clean = True  # change for debug

    if opts.bam:
        mreads = path.realpath(opts.bam)
        if not opts.biases and all(v !='raw' for v in opts.normalizations):
            raise Exception('ERROR: external BAM input, should provide path to'
                            ' biases file.')
        biases = opts.biases
    else:
        biases, mreads = load_parameters_fromdb(opts)
        mreads = path.join(opts.workdir, mreads)
        biases = path.join(opts.workdir, biases)
    if opts.biases:
        biases = opts.biases

    coord1         = opts.coord1
    coord2         = opts.coord2

    if coord2 and not coord1:
        coord1, coord2 = coord2, coord1

    if not coord1:
        region1 = None
        start1  = None
        end1    = None
        region2 = None
        start2  = None
        end2    = None
    else:
        try:
            crm1, pos1   = coord1.split(':')
            start1, end1 = pos1.split('-')
            region1 = crm1
            start1  = int(start1)
            end1    = int(end1)
        except ValueError:
            region1 = coord1
            start1  = None
            end1    = None
        if coord2:
            try:
                crm2, pos2   = coord2.split(':')
                start2, end2 = pos2.split('-')
                region2 = crm2
                start2  = int(start2)
                end2    = int(end2)
            except ValueError:
                region2 = coord2
                start2  = None
                end2    = None
        else:
            region2 = None
            start2  = None
            end2    = None

    outdir = path.join(opts.workdir, '05_sub-matrices')
    mkdir(outdir)
    tmpdir = path.join(opts.workdir, '05_sub-matrices',
                       '_tmp_sub-matrices_%s' % param_hash)
    mkdir(tmpdir)

    if region1:
        if region1:
            if not opts.quiet:
                stdout.write('\nExtraction of %s' % (region1))
            if start1:
                if not opts.quiet:
                    stdout.write(':%s-%s' % (start1, end1))
            else:
                if not opts.quiet:
                    stdout.write(' (full chromosome)')
            if region2:
                if not opts.quiet:
                    stdout.write(' intersection with %s' % (region2))
                if start2:
                    if not opts.quiet:
                        stdout.write(':%s-%s\n' % (start2, end2))
                else:
                    if not opts.quiet:
                        stdout.write(' (full chromosome)\n')
            else:
                if not opts.quiet:
                    stdout.write('\n')
    else:
        if not opts.quiet:
            stdout.write('\nExtraction of full genome\n')

    out_files = {}
    out_plots = {}
    norm_string = ('RAW' if 'raw' in opts.normalizations else 'NRM'
                   if 'norm' in opts.normalizations else 'DEC')

    if opts.matrix or opts.plot:
        bamfile = AlignmentFile(mreads, 'rb')
        sections = OrderedDict(zip(bamfile.references,
                                   [x for x in bamfile.lengths]))
        total = 0
        section_pos = dict()
        for crm in sections:
            section_pos[crm] = (total, total + sections[crm])
            total += sections[crm]
        for norm in opts.normalizations:
            printime('Getting %s matrices' % norm)
            matrix, bads1, bads2, regions, name, bin_coords = get_matrix(
                mreads, opts.reso,
                load(open(biases)) if biases and norm != 'raw' else None,
                normalization=norm,
                region1=region1, start1=start1, end1=end1,
                region2=region2, start2=start2, end2=end2,
                tmpdir=tmpdir, ncpus=opts.cpus,
                return_headers=True,
                nchunks=opts.nchunks, verbose=not opts.quiet,
                clean=clean)
            b1, e1, b2, e2 = bin_coords
            b1, e1 = 0, e1 - b1
            b2, e2 = 0, e2 - b2
            if opts.row_names:
                starts = [start1, start2]
                ends = [end1, end2]
                row_names = ((reg, p + 1 , p + opts.reso) for r, reg in enumerate(regions)
                             for p in range(starts[r] if r < len(starts) and starts[r] else 0,
                                            ends[r] if r < len(ends) and ends[r] else sections[reg],
                                            opts.reso))
            if opts.matrix:
                printime(' - Writing: %s' % norm)
                fnam = '%s_%s_%s%s.mat' % (norm, name,
                                           nicer(opts.reso).replace(' ', ''),
                                           ('_' + param_hash))
                out_files[norm_string] = path.join(outdir, fnam)
                out = open(path.join(outdir, fnam), 'w')
                for reg in regions:
                    out.write('# CRM %s\t%d\n' % (reg, sections[reg]))
                if region2:
                    out.write('# BADROWS %s\n' % (','.join([str(b) for b in bads1])))
                    out.write('# BADCOLS %s\n' % (','.join([str(b) for b in bads2])))
                else:
                    out.write('# MASKED %s\n' % (','.join([str(b) for b in bads1])))
                if opts.row_names:
                    out.write('\n'.join('%s\t%d\t%d\t' % (row_names.next()) +
                                        '\t'.join(str(matrix.get((i, j), 0))
                                                  for i in xrange(b1, e1))
                                        for j in xrange(b2, e2)) + '\n')
                else:
                    out.write('\n'.join('\t'.join(str(matrix.get((i, j), 0))
                                                  for i in xrange(b1, e1))
                                        for j in xrange(b2, e2)) + '\n')
                out.close()
            if opts.plot:
                cmap = plt.get_cmap(opts.cmap)
                if norm != 'raw':
                    cmap.set_bad('grey', 1.)
                printime(' - Plotting: %s' % norm)
                fnam = '%s_%s_%s%s.%s' % (norm, name,
                                          nicer(opts.reso).replace(' ', ''),
                                          ('_' + param_hash), opts.format)
                out_plots[norm_string] = path.join(outdir, fnam)
                fig = plt.figure(figsize=(15, 12))
                ax1 = plt.subplot(111)
                matrix = array([array([matrix.get((i, j), 0) for i in xrange(b1, e1)])
                                for j in xrange(b2, e2)])
                mini = np_min(matrix[nonzero(matrix)]) / 2.
                matrix[matrix==0] = mini
                m = zeros_like(matrix)
                for bad1 in bads1:
                    m[:,bad1] = 1
                    for bad2 in bads2:
                        m[bad2,:] = 1
                matrix = log2(ma.masked_array(matrix, m))
                plt.imshow(matrix, interpolation='None', origin='lower',
                           cmap=cmap, vmin=vmin, vmax=vmax)

                if len(regions) <= 2:
                    pltbeg1 = 0 if start1 is None else start1
                    pltend1 = sections[regions[0]] if end1 is None else end1
                    pltbeg2 = pltbeg1 if len(regions) == 1 else 0 if start2 is None else start2
                    pltend2 = pltend1 if len(regions) == 1 else sections[regions[-1]] if end2 is None else end2

                    plt.xlabel('%s:%d-%d' % (regions[0] , pltbeg1 if pltbeg1 else 1, pltend1))
                    plt.ylabel('%s:%d-%d' % (regions[-1], pltbeg2 if pltbeg2 else 1, pltend2))

                    pltend1 =  pltend1 // opts.reso * opts.reso
                    pltend2 =  pltend2 // opts.reso * opts.reso

                    xcuts = 2 if len(matrix[0]) <= 10 else 5 if len(matrix[0]) <= 20 else 10
                    x_pos = linspace(0, len(matrix[0]) // xcuts * xcuts, xcuts + 1)
                    plt.xticks(x_pos, [nicer(p if p else 1, coma=True) for p in range(pltbeg1, pltend1, (pltend1 - pltbeg1) // xcuts)],
                               rotation=-25, ha='left')

                    ycuts = 2 if len(matrix) <= 10 else 5 if len(matrix) <= 20 else 10
                    y_pos = linspace(0, len(matrix) // ycuts * ycuts, ycuts + 1)
                    plt.yticks(y_pos, [nicer(p if p else 1, coma=True) for p in range(pltbeg2, pltend2, (pltend2 - pltbeg2) // ycuts)])

                    plt.xlim(-0.5, len(matrix[0]) - 0.5)
                    plt.ylim(-0.5, len(matrix) - 0.5)
                else:
                    vals = [0]
                    keys = ['']
                    for crm in regions:
                        vals.append(section_pos[crm][0] / opts.reso)
                        keys.append(crm)
                    vals.append(section_pos[crm][1] / opts.reso)
                    ax1.set_yticks(vals)
                    ax1.set_yticklabels('')
                    ax1.set_yticks([float(vals[i]+vals[i+1])/2
                                    for i in xrange(len(vals) - 1)], minor=True)
                    ax1.set_yticklabels(keys, minor=True)
                    for t in ax1.yaxis.get_minor_ticks():
                        t.tick1On = False
                        t.tick2On = False

                    ax1.set_xticks(vals)
                    ax1.set_xticklabels('')
                    ax1.set_xticks([float(vals[i]+vals[i+1])/2
                                    for i in xrange(len(vals) - 1)], minor=True)
                    ax1.set_xticklabels(keys, minor=True)
                    for t in ax1.xaxis.get_minor_ticks():
                        t.tick1On = False
                        t.tick2On = False
                    plt.xlabel('Chromosomes')
                    plt.ylabel('Chromosomes')
                    plt.xlim(-0.5, len(matrix[0]) - 0.5)
                    plt.ylim(-0.5, len(matrix) - 0.5)

                plt.title('Region: %s, normalization: %s, resolution: %s' % (
                    name, norm, nicer(opts.reso)))
                cbar = plt.colorbar()
                cbar.ax.set_ylabel('Hi-C Log2 interaction count')
                if opts.interactive:
                    plt.show()
                    plt.close('all')
                else:
                    tadbit_savefig(path.join(outdir, fnam))
    if not opts.matrix and not opts.only_plot:
        printime('Getting and writing matrices')
        out_files = write_matrix(mreads, opts.reso,
                                 load(open(biases)) if biases else None,
                                 outdir, filter_exclude=opts.filter,
                                 normalizations=opts.normalizations,
                                 region1=region1, start1=start1, end1=end1,
                                 region2=region2, start2=start2, end2=end2,
                                 tmpdir=tmpdir, append_to_tar=None, ncpus=opts.cpus,
                                 nchunks=opts.nchunks, verbose=not opts.quiet,
                                 extra=param_hash, clean=clean)

    printime('Cleaning and saving to db.')
    if clean:
        system('rm -rf %s '% tmpdir)
    finish_time = time.localtime()

    save_to_db(opts, launch_time, finish_time, out_files, out_plots)


def save_to_db(opts, launch_time, finish_time, out_files, out_plots):
    if 'tmpdb' in opts and opts.tmpdb:
        # check lock
        while path.exists(path.join(opts.workdir, '__lock_db')):
            time.sleep(0.5)
        # close lock
        open(path.join(opts.workdir, '__lock_db'), 'a').close()
        # tmp file
        dbfile = opts.tmpdb
        try: # to copy in case read1 was already mapped for example
            copyfile(path.join(opts.workdir, 'trace.db'), dbfile)
        except IOError:
            pass
    else:
        dbfile = path.join(opts.workdir, 'trace.db')
    con = lite.connect(dbfile)
    with con:
        cur = con.cursor()
        try:
            parameters = digest_parameters(opts, get_md5=False, extra=['quiet'])
            param_hash = digest_parameters(opts, get_md5=True , extra=['quiet'])
            cur.execute("""
            insert into JOBs
            (Id  , Parameters, Launch_time, Finish_time, Type , Parameters_md5)
            values
            (NULL,       '%s',        '%s',        '%s', 'Bin',           '%s')
            """ % (parameters,
                   time.strftime("%d/%m/%Y %H:%M:%S", launch_time),
                   time.strftime("%d/%m/%Y %H:%M:%S", finish_time), param_hash))
        except lite.IntegrityError:
            pass
        except lite.OperationalError:
            try:
                cur.execute("""
                create table PATHs
                (Id integer primary key,
                JOBid int, Path text, Type text,
                unique (Path))""")
            except lite.OperationalError:
                pass  # may append when mapped files cleaned
            cur.execute("""
            create table JOBs
               (Id integer primary key,
                Parameters text,
                Launch_time text,
                Finish_time text,
                Type text,
                Parameters_md5 text,
                unique (Parameters_md5))""")
            cur.execute("""
            insert into JOBs
            (Id  , Parameters, Launch_time, Finish_time, Type , Parameters_md5)
            values
            (NULL,       '%s',        '%s',        '%s', 'Bin',           '%s')
            """ % (parameters,
                   time.strftime("%d/%m/%Y %H:%M:%S", launch_time),
                   time.strftime("%d/%m/%Y %H:%M:%S", finish_time), param_hash))
        jobid = get_jobid(cur)
        for fnam in out_files:
            add_path(cur, out_files[fnam], fnam + '_MATRIX', jobid, opts.workdir)
        for fnam in out_plots:
            add_path(cur, out_plots[fnam], fnam + '_FIGURE', jobid, opts.workdir)
        if not opts.quiet:
            print_db(cur, 'JOBs')
            print_db(cur, 'PATHs')
    if 'tmpdb' in opts and opts.tmpdb:
        # copy back file
        copyfile(dbfile, path.join(opts.workdir, 'trace.db'))
        remove(dbfile)
    # release lock
    try:
        remove(path.join(opts.workdir, '__lock_db'))
    except OSError:
        pass


def check_options(opts):
    mkdir(opts.workdir)

    # transform filtering reads option
    opts.filter = filters_to_bin(opts.filter)

    # check resume
    if not path.exists(opts.workdir):
        raise IOError('ERROR: workdir not found.')

    # for lustre file system....
    if 'tmpdb' in opts and opts.tmpdb:
        dbdir = opts.tmpdb
        # tmp file
        dbfile = 'trace_%s' % (''.join([ascii_letters[int(random() * 52)]
                                        for _ in range(10)]))
        opts.tmpdb = path.join(dbdir, dbfile)
        try:
            copyfile(path.join(opts.workdir, 'trace.db'), opts.tmpdb)
        except IOError:
            pass

    # number of cpus
    if opts.cpus == 0:
        opts.cpus = cpu_count()
    else:
        opts.cpus = min(opts.cpus, cpu_count())

    # check if job already run using md5 digestion of parameters
    if already_run(opts):
        if 'tmpdb' in opts and opts.tmpdb:
            remove(path.join(dbdir, dbfile))
        exit('WARNING: exact same job already computed, see JOBs table above')


def populate_args(parser):
    """
    parse option from call
    """
    parser.formatter_class=lambda prog: HelpFormatter(prog, width=95,
                                                      max_help_position=27)

    oblopt = parser.add_argument_group('Required options')
    glopts = parser.add_argument_group('General options')
    rfiltr = parser.add_argument_group('Read filtering options')
    normpt = parser.add_argument_group('Normalization options')
    outopt = parser.add_argument_group('Output options')
    pltopt = parser.add_argument_group('Plotting options')

    oblopt.add_argument('-w', '--workdir', dest='workdir', metavar="PATH",
                        action='store', default=None, type=str, required=True,
                        help='''path to working directory (generated with the
                        tool tadbit mapper)''')

    oblopt.add_argument('-r', '--resolution', dest='reso', metavar="INT",
                        action='store', default=None, type=int, required=True,
                        help='''resolution at which to output matrices''')

    glopts.add_argument('--bam', dest='bam', metavar="PATH",
                        action='store', default=None, type=str,
                        help='''path to a TADbit-generated BAM file with
                        all reads (other wise the tool will guess from the
                        working directory database)''')

    glopts.add_argument('-j', '--jobid', dest='jobid', metavar="INT",
                        action='store', default=None, type=int,
                        help='''Use as input data generated by a job with a given
                        jobid. Use tadbit describe to find out which.''')

    glopts.add_argument('--force', dest='force', action='store_true',
                        default=False,
                        help='overwrite previously run job')

    glopts.add_argument('-q', '--quiet', dest='quiet', action='store_true',
                        default=False,
                        help='remove all messages')

    glopts.add_argument('--tmpdb', dest='tmpdb', action='store', default=None,
                        metavar='PATH', type=str,
                        help='''if provided uses this directory to manipulate the
                        database''')

    glopts.add_argument('--nchunks', dest='nchunks', action='store', default=None,
                        type=int,
                        help='''maximum number of chunks into which to
                        cut the BAM''')

    glopts.add_argument("-C", "--cpus", dest="cpus", type=int,
                        default=0, help='''[%(default)s] Maximum number of CPU
                        cores  available in the execution host. If higher
                        than 1, tasks with multi-threading
                        capabilities will enabled (if 0 all available)
                        cores will be used''')

    outopt.add_argument('--matrix', dest='matrix', action='store_true',
                        default=False,
                        help='''Write text matrix in multiple columns. By
                        defaults matrices are written in BED-like format (also
                        only way to get a raw matrix with all values including
                        the ones in masked columns).''')

    outopt.add_argument('--rownames', dest='row_names', action='store_true',
                        default=False,
                        help='To store row names in the output text matrix.')

    pltopt.add_argument('--plot', dest='plot', action='store_true',
                        default=False,
                        help='[%(default)s] Plot matrix in desired format.')

    outopt.add_argument('--only_plot', dest='only_plot', action='store_true',
                        default=False,
                        help='[%(default)s] Skip writing matrix in text format.')

    outopt.add_argument('--interactive', dest='interactive', action='store_true',
                        default=False,
                        help='''[%(default)s] Open matplotlib interactive plot
                        (nothing will be saved).''')

    pltopt.add_argument('--cmap', dest='cmap', action='store',
                        default='viridis',
                        help='[%(default)s] Matplotlib color map to use.')

    pltopt.add_argument('--format', dest='format', action='store',
                        default='png',
                        help='[%(default)s] plot file format.')

    pltopt.add_argument('--zrange', dest='zrange', action='store',
                        default=None,
                        help='''Range, in log2 scale of the color scale.
                        i.e.: --zrange 2,-2''')

    outopt.add_argument('-c', '--coord', dest='coord1',  metavar='',
                        default=None, help='''Coordinate of the region to
                        retrieve. By default all genome, arguments can be
                        either one chromosome name, or the coordinate in
                        the form: "-c chr3:110000000-120000000"''')

    outopt.add_argument('-c2', '--coord2', dest='coord2',  metavar='',
                        default=None, help='''Coordinate of a second region to
                        retrieve the matrix in the intersection with the first
                        region.''')

    normpt.add_argument('--biases',   dest='biases', metavar="PATH",
                        action='store', default=None, type=str,
                        help='''path to file with pre-calculated biases by
                        columns''')

    normpt.add_argument('--norm', dest='normalizations', metavar="STR",
                        action='store', default=['raw'], type=str, nargs='+',
                        choices=['norm', 'decay', 'raw'],
                        help='''[%(default)s] normalization(s) to apply.
                        Choices are: [%(choices)s]''')

    rfiltr.add_argument('-F', '--filter', dest='filter', nargs='+',
                        type=int, metavar='INT', default=[1, 2, 3, 4, 6, 7, 9, 10],
                        choices = range(1, 11),
                        help=("""[%(default)s] Use filters to define a set os
                        valid pair of reads e.g.:
                        '--apply 1 2 3 4 8 9 10'. Where these numbers""" +
                              "correspond to: %s" % (', '.join(
                                  ['%2d: %15s' % (k, MASKED[k]['name'])
                                   for k in MASKED]))))

    outopt.add_argument('--only_txt', dest='only_txt', action='store_true',
                        default=False,
                        help='Save only text file for matrices, not images')

    rfiltr.add_argument('--valid', dest='only_valid', action='store_true',
                        default=False,
                        help='input BAM file contains only valid pairs (already filtered).')


def load_parameters_fromdb(opts):
    if opts.tmpdb:
        dbfile = opts.tmpdb
    else:
        dbfile = path.join(opts.workdir, 'trace.db')
    if not path.exists(dbfile):
        raise Exception('ERROR: DB file: %s not found.' % dbfile)
    con = lite.connect(dbfile)
    with con:
        cur = con.cursor()
        if not opts.jobid:
            # get the JOBid of the parsing job
            try:
                cur.execute("""
                select distinct Id from JOBs
                where Type = '%s'
                """ % ('Normalize' if opts.normalizations != ('raw', ) else 'Filter'))
                jobids = cur.fetchall()
                parse_jobid = jobids[0][0]
            except IndexError:
                cur.execute("""
                select distinct Id from JOBs
                where Type = '%s'
                """ % ('Filter'))
                jobids = cur.fetchall()
                try:
                    parse_jobid = jobids[0][0]
                except IndexError:
                    parse_jobid = 1
            if len(jobids) > 1:
                found = False
                if opts.normalizations != ('raw', ):
                    cur.execute("""
                    select distinct JOBid from NORMALIZE_OUTPUTs
                    where Resolution = %d
                    """ % (opts.reso))
                    jobs = cur.fetchall()
                    try:
                        parse_jobid = jobs[0][0]
                        found = True
                    except IndexError:
                        found = False
                    if len(jobs ) > 1:
                        found = False
                if not found:
                    raise Exception('ERROR: more than one possible input found, use'
                                    '"tadbit describe" and select corresponding '
                                    'jobid with --jobid')
        else:
            parse_jobid = opts.jobid
        # fetch path to parsed BED files
        # try:
        biases = mreads = reso = None
        if opts.normalizations != ('raw', ):
            try:
                cur.execute("""
                select distinct Path from PATHs
                where paths.jobid = %s and paths.Type = 'BIASES'
                """ % parse_jobid)
                biases = cur.fetchall()[0][0]

                cur.execute("""
                select distinct Path from PATHs
                inner join NORMALIZE_OUTPUTs on PATHs.Id = NORMALIZE_OUTPUTs.Input
                where NORMALIZE_OUTPUTs.JOBid = %d;
                """ % parse_jobid)
                mreads = cur.fetchall()[0][0]

                cur.execute("""
                select distinct Resolution from NORMALIZE_OUTPUTs
                where NORMALIZE_OUTPUTs.JOBid = %d;
                """ % parse_jobid)
                reso = int(cur.fetchall()[0][0])
                if reso != opts.reso:
                    warn('WARNING: input resolution does not match '
                         'the one of the precomputed normalization')
            except IndexError:
                warn('WARNING: normalization not found')
                cur.execute("""
                select distinct path from paths
                inner join filter_outputs on filter_outputs.pathid = paths.id
                where filter_outputs.name = 'valid-pairs' and paths.jobid = %s
                """ % parse_jobid)
                mreads = cur.fetchall()[0][0]
        else:
            cur.execute("""
            select distinct path from paths
            inner join filter_outputs on paths.type = 'HIC_BAM'
            where filter_outputs.name = 'valid-pairs' and paths.jobid = %s
            """ % parse_jobid)
            fetched = cur.fetchall()
            if len(fetched) > 1:
                raise Exception('ERROR: more than one item in the database')
            mreads = fetched[0][0]
        return biases, mreads
