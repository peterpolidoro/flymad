import os
if 'DISPLAY' not in os.environ:
    import matplotlib
    matplotlib.use('Agg')

import argparse
import glob
import subprocess
import cPickle as pickle
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.image as mimg

from scipy.stats import ttest_ind

import roslib; roslib.load_manifest('flymad')
import flymad.flymad_analysis_dan as flymad_analysis
import flymad.flymad_plot as flymad_plot
import madplot

from strawlab_mpl.spines import spine_placer

#need to support numpy datetime64 types for resampling in pandas
assert np.version.version in ("1.7.1", "1.6.1")
assert pd.version.version in ("0.11.0", "0.12.0")

EXPERIMENT_DURATION = 600

XLIM_10MIN = [-60,480]
XLABEL_10MIN = [0, 120, 240, 360, 480]

DR_COLORS = {'100hpc':flymad_plot.BLACK,
            '120hpc':flymad_plot.GREEN,
            '140hpc':flymad_plot.RED,
            '160hpc':flymad_plot.BLUE}

DIRECTED_COURTING_DIST = 50

def _get_targets(path, date):
    #first we look for a png file corresponding to the scored MP4 (for
    #consistency with the initial submission)

    def _mp4_click(image_path, cache_path):
        img = mimg.imread(image_path)
        fig1 = plt.figure()
        fig1.set_size_inches(12,8)
        fig1.subplots_adjust(hspace=0)
        ax1 = fig1.add_subplot(1,1,1)	

        #the original wide field camera was 659x494px. The rendered mp4 is 384px high
        #the widefield image is padded with a 10px margin, so it is technically 514 high.
        #scaling h=384->514 means new w=1371
        #
        #the image origin is top-left because matplotlib
        ax1.imshow(img, extent=[0,1371,514,0],zorder=0) #extent=[h_min,h_max,v_min,v_max]
        ax1.axis('off') 

        targets = []
        def _onclick(target):
            #subtract 10px for the margin
            xydict = {'x': target.xdata-10, 'y': target.ydata-10}
            targets.append(xydict)

        cid = fig1.canvas.mpl_connect('button_press_event', _onclick)
        plt.show()
        fig1.canvas.mpl_disconnect(cid)

        with open(cache_path, 'wb') as f:
            pickle.dump(targets, f, -1)

        return targets

    #cached results
    pata = os.path.join(path,'*%s*.mp4.png.madplot-cache' % date)
    mp4pngcache = glob.glob(pata)
    if len(mp4pngcache) == 1:
        return pickle.load( open(mp4pngcache[0],'rb') )

    patb = os.path.join(path,'*%s*.mp4.png' % date)
    mp4png = glob.glob(patb)
    if len(mp4png) == 1:
        return _mp4_click(mp4png[0], mp4png[0] + '.madplot-cache')

    patc = os.path.join(path,'*%s*.mp4' % date)
    mp4 = glob.glob(patc)
    if len(mp4) == 1:
        mp4 = mp4[0]
        mp4png = mp4 + '.png'
        #make a thumbnail
        subprocess.check_call("ffmpeg -i %s -vframes 1 -an -f image2 -y %s" % (mp4,mp4png),
                              shell=True)
        return _mp4_click(mp4png, mp4png + '.madplot-cache')

    print "WARNING: could not find\n\t", "\n\t".join((pata,patb,patc))

    return []

def prepare_data(path, only_laser, resample_bin, gts):
    data = {}

    #PROCESS SCORE FILES:
    pooldf = pd.DataFrame()
    for df,metadata in flymad_analysis.load_courtship_csv(path):
        csvfilefn,experimentID,date,time,genotype,laser,repID = metadata
        if laser != only_laser:
            print "\tskipping laser", laser
            continue

        if genotype not in gts:
            print "\tskipping genotype", genotype
            continue

        targets = _get_targets(path, date)
        assert len(targets) == 4
        targets = pd.DataFrame(targets)
        targets = (targets + 0.5).astype(int)

        #CALCULATE DISTANCE FROM TARGETs, KEEP MINIMUM AS dtarget
        if targets is not None:
            dist = pd.DataFrame.copy(df, deep=True)
            dist['x0'] = df['x'] - targets.ix[0,'x']
            dist['y0'] = df['y'] - targets.ix[0,'y']
            dist['x1'] = df['x'] - targets.ix[1,'x']
            dist['y1'] = df['y'] - targets.ix[1,'y']
            dist['x2'] = df['x'] - targets.ix[2,'x']
            dist['y2'] = df['y'] - targets.ix[2,'y']
            dist['x3'] = df['x'] - targets.ix[3,'x']
            dist['y3'] = df['y'] - targets.ix[3,'y']
            dist['d0'] = ((dist['x0'])**2 + (dist['y0'])**2)**0.5
            dist['d1'] = ((dist['x1'])**2 + (dist['y1'])**2)**0.5
            dist['d2'] = ((dist['x2'])**2 + (dist['y2'])**2)**0.5
            dist['d3'] = ((dist['x3'])**2 + (dist['y3'])**2)**0.5
            df['dtarget'] = dist.ix[:,'d0':'d3'].min(axis=1)               
        else:
            df['dtarget'] = 0

        duration = (df.index[-1] - df.index[0]).total_seconds()
        if duration < EXPERIMENT_DURATION:
            print "\tmissing data", csvfilefn
            continue

        print "\t%ss experiment" % duration

        #resample into 5S bins
        df = df.resample(resample_bin)
        #trim dataframe
        df = df.head(flymad_analysis.get_num_rows(EXPERIMENT_DURATION, resample_bin))
        tb = flymad_analysis.get_resampled_timebase(EXPERIMENT_DURATION, resample_bin)

        #fix laser_state due to resampling
        df['laser_state'][df['laser_state'] > 0] = 1
        df['zx_binary'] = (df['zx'] > 0).values.astype(float)

        t0idx = np.argmax(np.gradient(df['laser_state'].values > 0))
        t0 = tb[t0idx]
        df['t'] = tb - t0

        #groupby on float times is slow. make a special align column 
        df['t_align'] = np.array(range(0,len(df))) - t0idx

        df['obj_id'] = flymad_analysis.create_object_id(date,time)
        df['Genotype'] = genotype
        df['lasergroup'] = laser
        df['RepID'] = repID

        pooldf = pd.concat([pooldf, df])   

    data = {}
    for gt in gts:
        gtdf = pooldf[pooldf['Genotype'] == gt]

        lgs = gtdf['lasergroup'].unique()
        if len(lgs) != 1:
            raise Exception("only one lasergroup handled for gt %s: not %s" % (
                             gt, lgs))

        grouped = gtdf.groupby(['t_align'], as_index=False)
        data[gt] = dict(mean=grouped.mean().astype(float),
                        std=grouped.std().astype(float),
                        n=grouped.count().astype(float),
                        first=grouped.first(),
                        df=gtdf)

    return data

def run_stats (path, dfs):

    (expdf, expmean, expstd, expn,
            exp2df, exp2mean, exp2std, exp2n,
            ctrldf, ctrlmean, ctrlstd, ctrln,
            pooldf) = dfs
 
    print type(pooldf), pooldf.shape 
    p_values = pd.DataFrame()  
    df_ctrl = pooldf[pooldf['Genotype'] == CTRL_GENOTYPE]
    df_exp1 = pooldf[pooldf['Genotype'] == EXP_GENOTYPE]
    df_exp2 = pooldf[pooldf['Genotype'] == EXP_GENOTYPE2]
    df_exp2['Genotype'] = 'VT40347GP'
    df_ctrl = df_ctrl[df_ctrl['t'] <= 485]
    df_exp1 = df_exp1[df_exp1['t'] <= 485]
    df_exp2 = df_exp2[df_exp2['t'] <= 485]
    df_ctrl = df_ctrl[df_ctrl['t'] >=-120]
    df_exp1 = df_exp1[df_exp1['t'] >=-120]
    df_exp2 = df_exp2[df_exp2['t'] >=-120]
    bins = np.linspace(-120,485,122)  # 5 second bins -120 to 485
    binned_ctrl = pd.cut(df_ctrl['t'], bins, labels= bins[:-1])
    binned_exp1 = pd.cut(df_exp1['t'], bins, labels= bins[:-1])
    binned_exp2 = pd.cut(df_exp2['t'], bins, labels= bins[:-1])
    for x in binned_ctrl.levels:               
        testctrl = df_ctrl['zx'][binned_ctrl == x]
        test1 = df_exp1['zx'][binned_exp1 == x]
        test2 = df_exp2['zx'][binned_exp2 == x]
        hval1, pval1 = ttest_ind(test1, testctrl)
        hval2, pval2 = ttest_ind(test2, testctrl) #too many identical values (zeros) in controls, so cannot do Kruskal.
        dftemp = pd.DataFrame({'Total_bins': binsize , 'Bin_number': x, 'P1': pval1, 'P2':pval2}, index=[x])
        p_values = pd.concat([p_values, dftemp])
    p_values1 = p_values[['Total_bins', 'Bin_number', 'P1']]
    p_values1.columns = ['Total_bins', 'Bin_number', 'P']
    p_values2 = p_values[['Total_bins', 'Bin_number', 'P2']]
    p_values2.columns = ['Total_bins', 'Bin_number', 'P']
    return p_values1, p_values2

def fit_to_curve ( p_values ):
    x = np.array(p_values['Bin_number'])
    logs = -1*(np.log(p_values['P']))
    y = np.array(logs)
    order = 6 #DEFINE ORDER OF POLYNOMIAL HERE.
    poly_params = np.polyfit(x,y,order)
    polynom = np.poly1d(poly_params)
    xPoly = np.linspace(min(x), max(x), 100)
    yPoly = polynom(xPoly)
    fig1 = plt.figure()
    ax = fig1.add_subplot(1,1,1)
    ax.plot(x, y, 'o', xPoly, yPoly, '-g')
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('-log(p)')
    #plt.axhline(y=1.30103, color='k-')
    print polynom #lazy dan can't use python to solve polynomial eqns. boo.
    return (x, y, xPoly, yPoly, polynom)

def plot_data(path, laser, dfs, autoscale=False):

    COLORS = {'wtrpmyc':flymad_plot.BLACK,
              'wGP':flymad_plot.RED,
              '40347trpmyc':flymad_plot.ORANGE,
              'G323':flymad_plot.BLUE,
              '40347':flymad_plot.GREEN}

    figname = laser + '_' + '_'.join(dfs)
    if autoscale:
        figname += "_AUTOSCALE"

    datasets = {}
    for gt in dfs:
        if flymad_analysis.genotype_is_exp(gt):
            order = 1
        elif flymad_analysis.genotype_is_ctrl(gt):
            order = 2
        else:
            order = 3
        gtdf = dfs[gt]
        datasets[gt] = dict(xaxis=gtdf['mean']['t'].values,
                            value=gtdf['mean']['zx'].values,
                            std=gtdf['std']['zx'].values,
                            n=gtdf['n']['zx'].values,
                            label=flymad_analysis.human_label(gt, specific=True),
                            order=order,
                            color=COLORS[gt],
                            df=gtdf['df'],
                            N=len(gtdf['df']['obj_id'].unique()))
    ctrlmean = dfs['wtrpmyc']['mean']

    figure_title = "Courtship Wingext 10min (%s)" % laser
    fig = plt.figure(figure_title)
    ax = fig.add_subplot(1,1,1)

    _,_,figs = flymad_plot.plot_timeseries_with_activation(ax,
                    targetbetween=dict(xaxis=ctrlmean['t'].values,
                                       where=ctrlmean['laser_state'].values>0),
                    sem=True,
                    note="laser %s\n" % flymad_analysis.laser_desc(laser),
                    individual={k:{'groupby':'obj_id','xaxis':'t','yaxis':'zx'} for k in ('wGP','40347trpmyc')},
                    individual_title=figure_title + ' Individual Traces',
                    **datasets
    )

    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Wing extension index')

    if not autoscale:
        ax.set_ylim([0,0.6])
        ax.set_xlim(XLIM_10MIN)

    flymad_plot.retick_relabel_axis(ax, XLABEL_10MIN, [0,0.3,0.6])

    fig.savefig(flymad_plot.get_plotpath(path,"following_and_WingExt_%s.png" % figname), bbox_inches='tight')
    fig.savefig(flymad_plot.get_plotpath(path,"following_and_WingExt_%s.svg" % figname), bbox_inches='tight')

    for efigname, efig in figs.iteritems():
        efig.savefig(flymad_plot.get_plotpath(path,"following_and_WingExt_%s_individual_%s.png" % (figname, efigname)), bbox_inches='tight')

    datasets = {}
    for gt in dfs:
        if flymad_analysis.genotype_is_exp(gt):
            order = 1
        elif flymad_analysis.genotype_is_ctrl(gt):
            order = 2
        else:
            order = 3
        gtdf = dfs[gt]
        datasets[gt] = dict(xaxis=gtdf['mean']['t'].values,
                            value=gtdf['mean']['dtarget'].values,
                            std=gtdf['std']['dtarget'].values,
                            n=gtdf['n']['dtarget'].values,
                            label=flymad_analysis.human_label(gt, specific=True),
                            order=order,
                            color=COLORS[gt],
                            df=gtdf['df'],
                            N=len(gtdf['df']['obj_id'].unique()))
    ctrlmean = dfs['wtrpmyc']['mean']

    figure_title = "Courtship Dtarget 10min (%s)" % laser
    fig = plt.figure(figure_title)
    ax = fig.add_subplot(1,1,1)

    _,_,figs = flymad_plot.plot_timeseries_with_activation(ax,
                    targetbetween=dict(xaxis=ctrlmean['t'].values,
                                       where=ctrlmean['laser_state'].values>0),
                    sem=True,
                    legend_location='lower right',
                    note="laser %s\n" % flymad_analysis.laser_desc(laser),
                    individual={k:{'groupby':'obj_id','xaxis':'t','yaxis':'dtarget'} for k in ('wGP','40347trpmyc')},
                    individual_title=figure_title + ' Individual Traces',
                    **datasets
    )

    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Distance (px)')

    if not autoscale:
        ax.set_ylim([40,160])
        ax.set_xlim(XLIM_10MIN)

    flymad_plot.retick_relabel_axis(ax, XLABEL_10MIN, [40,0,80,120,160])

    fig.savefig(flymad_plot.get_plotpath(path,"following_and_dtarget_%s.png" % figname), bbox_inches='tight')
    fig.savefig(flymad_plot.get_plotpath(path,"following_and_dtarget_%s.svg" % figname), bbox_inches='tight')

    for efigname, efig in figs.iteritems():
        efig.savefig(flymad_plot.get_plotpath(path,"following_and_dtarget_%s_individual_%s.png" % (figname, efigname)), bbox_inches='tight')

def plot_dose_response(path, bin_size, exp_gt, data):
    plots_to_save = []

    note = "%s\nbin %s\n" % (exp_gt, bin_size)

    laser_court = {}
    laser_dtarget = {}
    laser_dtarget_we = {}
    stat_groups = []
    for laser in sorted(data.keys()):
        expdf = data[laser][exp_gt]
        stat_groups.append(laser)

        laser_court[laser] = dict(xaxis=expdf['mean']['t'].values,
                                  value=expdf['mean']['zx'].values,
                                  std=expdf['std']['zx'].values,
                                  n=expdf['n']['zx'].values,
                                  color=DR_COLORS[laser],
                                  label=flymad_analysis.laser_desc(laser),
                                  df=expdf['df'],
                                  N=len(expdf['df']['obj_id'].unique()))
        laser_dtarget[laser] = dict(xaxis=expdf['mean']['t'].values,
                                    value=expdf['mean']['dtarget'].values,
                                    std=expdf['std']['dtarget'].values,
                                    n=expdf['n']['dtarget'].values,
                                    color=DR_COLORS[laser],
                                    label=flymad_analysis.laser_desc(laser),
                                    df=expdf['df'],
                                    N=len(expdf['df']['obj_id'].unique()))

        #keep only values where there was WE
        non_grouped_df = expdf['df']
        wedf = non_grouped_df[non_grouped_df['zx'] > 0]
        laser_dtarget_we[laser] = dict(xaxis=wedf['t'].values,
                                       value=wedf['dtarget'].values,
                                       color=DR_COLORS[laser],
                                       label=flymad_analysis.laser_desc(laser),
                                       df=wedf,
                                       N=len(wedf['obj_id'].unique()))


    fname_prefix = flymad_plot.get_plotpath(path,'csv_courtship_DR_dtarget')
    madplot.view_pairwise_stats_plotly(laser_dtarget, stat_groups, fname_prefix,
                                       align_colname='t',
                                       stat_colname='dtarget',
                                       layout_title='p-values for Dose-Response of distance',
                                       )

    fname_prefix = flymad_plot.get_plotpath(path,'csv_courtship_DR_wei')
    madplot.view_pairwise_stats_plotly(laser_court, stat_groups, fname_prefix,
                                       align_colname='t',
                                       stat_colname='zx',
                                       layout_title='p-values for Dose-Response of WEI',
                                       )


    #all D/R experiments were identical, so take activation times from the
    #last one
    targetbetween = dict(xaxis=expdf['mean']['t'].values,
                         where=expdf['mean']['laser_state'].values>0)

    fig = plt.figure("Courtship Wingext 10min D/R")
    ax = fig.add_subplot(1,1,1)
    flymad_plot.plot_timeseries_with_activation(ax,
                    targetbetween=targetbetween,
                    sem=True,
                    note=note,
                    **laser_court
    )
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Wing extension index')
    ax.set_xlim(XLIM_10MIN)
    ax.set_ylim([0,1])
    flymad_plot.retick_relabel_axis(ax, XLABEL_10MIN, [0,0.5,1])
    plots_to_save.append( ("DR_following_and_WingExt",fig,ax) )

    fig = plt.figure("Courtship Dtarget 10min D/R")
    ax = fig.add_subplot(1,1,1)
    flymad_plot.plot_timeseries_with_activation(ax,
                    targetbetween=targetbetween,
                    sem=True,
                    note=note,
                    **laser_dtarget
    )
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Distance (px)')
    ax.set_xlim(XLIM_10MIN)
    ax.set_ylim([20,150])
    flymad_plot.retick_relabel_axis(ax, XLABEL_10MIN, [40,80,120])
    plots_to_save.append( ("DR_following_and_dtarget",fig,ax) )

    for figname,fig,ax in plots_to_save:
        fig.savefig(flymad_plot.get_plotpath(path,"%s.png" % figname), bbox_inches='tight')
        fig.savefig(flymad_plot.get_plotpath(path,"%s.svg" % figname), bbox_inches='tight')

def plot_dose_response_dtarget_by_wei(path, bin_size, exp_gt, data):

    plots_to_save = []
    note = "%s\nbin %s\n" % (exp_gt, bin_size)

    laser_dtarget = {}
    laser_dtarget_we = {}
    laser_dtarget_prop = {}

    for laser in sorted(data.keys()):
        expdf = data[laser][exp_gt]
        non_grouped_df = expdf['df']

        laser_dtarget[laser] = dict(value=non_grouped_df['dtarget'].values,
                                    color=DR_COLORS[laser],
                                    label=flymad_analysis.laser_desc(laser))

        #keep only values where there was WE
        wedf = non_grouped_df[non_grouped_df['zx_binary'] > 0]
        gwedf = wedf.groupby('t_align').mean()
        laser_dtarget_we[laser] = dict(xaxis=gwedf['t'].values,
                                       value=gwedf['dtarget'].values,
                                       color=DR_COLORS[laser],
                                       label=flymad_analysis.laser_desc(laser),
                                       df=wedf,
                                       N=len(gwedf['obj_id'].unique()))

        #caculate the proportion
        proportions = []
        grouped = non_grouped_df.groupby('t_align')
        for _t,_rows in grouped:
            rows = _rows[['zx_binary','dtarget']]
#            print _t
#            print rows
            nwei = rows['zx_binary'].sum()
            if nwei > 0:
                nwei_and_close = len(rows[(rows['dtarget'] < DIRECTED_COURTING_DIST) & (rows['zx_binary'] > 0)])
                proportions.append( float(nwei_and_close) / nwei )
            else:
                proportions.append( np.nan )

        laser_dtarget_prop[laser] = dict(xaxis=grouped['t'].mean().values,
                                        value=np.array(proportions),
                                        color=DR_COLORS[laser],
                                        label=flymad_analysis.laser_desc(laser),
                                        N=len(grouped['obj_id'].unique()))

    #all D/R experiments were identical, so take activation times from the
    #last one
    targetbetween = dict(xaxis=expdf['mean']['t'].values,
                         where=expdf['mean']['laser_state'].values>0)

    fig = plt.figure("Courtship Dtarget 10min D/R When WEI")
    ax = fig.add_subplot(1,1,1)
    flymad_plot.plot_timeseries_with_activation(ax,
                    targetbetween=targetbetween,
                    downsample=500,
                    note=note,
                    linestyle='',marker='o',markersize=8,
                    **laser_dtarget_we
    )
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Distance (px)')
    ax.set_xlim(XLIM_10MIN)
    ax.set_ylim([20,110])
    flymad_plot.retick_relabel_axis(ax, XLABEL_10MIN, [50,100])
    plots_to_save.append( ("DR_following_and_dtarget_wei",fig,ax) )

    fig = plt.figure("Courtship Dtarget 10min D/R WEI Proportion")
    ax = fig.add_subplot(1,1,1)
    flymad_plot.plot_timeseries_with_activation(ax,
                    targetbetween=targetbetween,
                    downsample=500,
                    note="%sdist < %s\n" % (note, DIRECTED_COURTING_DIST),
                    linestyle='',marker='o',markersize=8,
                    **laser_dtarget_prop
    )
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Proportion')
    ax.set_xlim(XLIM_10MIN)
    ax.set_ylim([0,1])
    flymad_plot.retick_relabel_axis(ax, XLABEL_10MIN, [0,0.5,1.0])
    plots_to_save.append( ("DR_following_and_dtarget_wei_prop",fig,ax) )

    fig = plt.figure("Courtship WEI Hist 10min D/R")
    ax = fig.add_subplot(1,1,1)
    vals = []; colors = []; lbls = []
    for k in laser_dtarget:
        ds = laser_dtarget[k]
        #remove nans
        val = ds['value']
        vals.append(val[np.isfinite(val)])
        lbls.append(ds.get('label',k)),
        colors.append(ds['color'])
    ax.hist(vals,40,normed=True,color=colors,label=lbls,edgecolor='none')
    spine_placer(ax, location='left,bottom')
    ax.legend()
    ax.set_ylabel('Probability')
    ax.set_xlabel('Distance (px)')
    ax.set_ylim([0,0.025])
    ax.set_xlim([0,172])
    flymad_plot.retick_relabel_axis(ax, [0,50,100,150], [0,0.01,0.02])
    plots_to_save.append( ("DR_following_and_dtarget_wei_hist",fig,ax) )

    for figname,fig,ax in plots_to_save:
        fig.savefig(flymad_plot.get_plotpath(path,"%s.png" % figname), bbox_inches='tight')
        fig.savefig(flymad_plot.get_plotpath(path,"%s.svg" % figname), bbox_inches='tight')

def plot_dose_response_wei_by_proximity(path, bin_size, exp_gt, data):

    plots_to_save = []
    note = "%s\nbin %s\n" % (exp_gt, bin_size)

    wei_ext = {}
    wei_ext_close = {}
    wei_ext_far = {}

    for laser in sorted(data.keys()):
        expdf = data[laser][exp_gt]
        non_grouped_df = expdf['df']

        close = non_grouped_df['dtarget'] < DIRECTED_COURTING_DIST
        closedf = non_grouped_df[close]
        fardf = non_grouped_df[~close]

        grp = non_grouped_df.groupby('t_align')
        gdf = grp.mean()
        wei_ext[laser] = dict(xaxis=gdf['t'].values,
                              value=gdf['zx'].values,
#                              std=grp.std()['zx'].values,
#                              n=grp.count()['zx'].values,
                              color=DR_COLORS[laser],
                              label=flymad_analysis.laser_desc(laser),
                              df=non_grouped_df,
                              N=len(non_grouped_df['obj_id'].unique()))

        grp = closedf.groupby('t_align')
        cdf = grp.mean()
        wei_ext_close[laser] = dict(xaxis=cdf['t'].values,
                                    value=cdf['zx'].values,
#                                    std=grp.std()['zx'].values,
#                                    n=grp.count()['zx'].values,
                                    color=DR_COLORS[laser],
                                    label=flymad_analysis.laser_desc(laser),
                                    df=closedf,
                                    N=len(closedf['obj_id'].unique()))

        grp = fardf.groupby('t_align')
        fdf = grp.mean()
        wei_ext_far[laser] = dict(xaxis=fdf['t'].values,
                                  value=fdf['zx'].values,
#                                  std=grp.std()['zx'].values,
#                                  n=grp.count()['zx'].values,
                                  color=DR_COLORS[laser],
                                  label=flymad_analysis.laser_desc(laser),
                                  df=fardf,
                                  N=len(fardf['obj_id'].unique()))


    #all D/R experiments were identical, so take activation times from the
    #last one
    targetbetween = dict(xaxis=gdf['t'].values,
                         where=gdf['laser_state'].values>0)

    for dataseries,name in [(wei_ext, 'all'), (wei_ext_close, 'close'), (wei_ext_far, 'far')]:

        fig = plt.figure("Courtship WEI 10min D/R When %s" % name)
        ax = fig.add_subplot(1,1,1)
        flymad_plot.plot_timeseries_with_activation(ax,
                        targetbetween=targetbetween,
                        downsample=500,
                        note="%s (thresh %spx)\n%s" % (name,DIRECTED_COURTING_DIST,note),
                        sem=True,
                        **dataseries
        )
        ax.set_xlabel('Time (s)')
        ax.set_ylabel('Wing extension index')
        ax.set_xlim(XLIM_10MIN)
        ax.set_ylim([0,1])
        flymad_plot.retick_relabel_axis(ax, XLABEL_10MIN, [0,0.5,1])
        plots_to_save.append( ("DR_following_and_WingExt_%s" % name,fig,ax) )

    for figname,fig,ax in plots_to_save:
        fig.savefig(flymad_plot.get_plotpath(path,"%s.png" % figname), bbox_inches='tight')
        fig.savefig(flymad_plot.get_plotpath(path,"%s.svg" % figname), bbox_inches='tight')


if __name__ == "__main__":
    CTRL_GENOTYPE = 'wtrpmyc'
    EXP_GENOTYPE = 'wGP'
    EXP2_GENOTYPE = '40347trpmyc'
    CTRL2_GENOTYPE = 'G323'
    CTRL3_GENOTYPE = '40347'
    LASERS = [100,120,140]#,160]

    gts = EXP_GENOTYPE, EXP2_GENOTYPE, CTRL_GENOTYPE, CTRL2_GENOTYPE, CTRL3_GENOTYPE

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('path', nargs=1, help='path to csv files')
    parser.add_argument('--only-plot', action='store_true', default=False)
    parser.add_argument('--show', action='store_true', default=False)
    parser.add_argument('--laser', default='140hpc', help='laser specifier')
    parser.add_argument('--examples', default=False, action='store_true')

    args = parser.parse_args()
    path = args.path[0]

    if args.examples:
        bin_size = '1S'
    else:
        bin_size = '5S'
    cache_fname = os.path.join(path,'courtship_%s.madplot-cache' % bin_size)
    cache_args = (args.laser, gts, bin_size)
    dfs = None
    if args.only_plot:
        dfs = madplot.load_bagfile_cache(cache_args, cache_fname)
    if dfs is None:
        dfs = prepare_data(path, args.laser, bin_size, gts)
        madplot.save_bagfile_cache(dfs, cache_args, cache_fname)

    fname_prefix = flymad_plot.get_plotpath(path,'csv_courtship_WEI')
    madplot.view_pairwise_stats_plotly(dfs, gts, fname_prefix,
                                       align_colname='t',
                                       stat_colname='zx',
                                       )

    fname_prefix = flymad_plot.get_plotpath(path,'csv_courtship_dtarget')
    madplot.view_pairwise_stats_plotly(dfs, gts, fname_prefix,
                                       align_colname='t',
                                       stat_colname='dtarget',
                                       )

    plot_data(path, args.laser, dfs, autoscale=args.examples)
    if args.examples:
        if args.show:
            plt.show()
        sys.exit(0)

    bin_size = '5S'
    cache_fname = os.path.join(path,'courtship_dr_%s.madplot-cache' % bin_size)
    cache_args = bin_size, EXP_GENOTYPE, LASERS
    data = None
    if args.only_plot:
        data = madplot.load_bagfile_cache(cache_args, cache_fname)
    if data is None:
        data = {}
        for laser in LASERS:
            laser = '%dhpc' % laser
            data[laser] = prepare_data(path, laser, bin_size, [EXP_GENOTYPE])
        madplot.save_bagfile_cache(data, cache_args, cache_fname)
    plot_dose_response(path, bin_size, EXP_GENOTYPE, data)

    bin_size = '10L'
    cache_fname = os.path.join(path,'courtship_dr_%s.madplot-cache' % bin_size)
    cache_args = bin_size, EXP_GENOTYPE, LASERS
    data = None
    if args.only_plot:
        data = madplot.load_bagfile_cache(cache_args, cache_fname)
    if data is None:
        data = {}
        for laser in LASERS:
            laser = '%dhpc' % laser
            data[laser] = prepare_data(path, laser, bin_size, [EXP_GENOTYPE])
        madplot.save_bagfile_cache(data, cache_args, cache_fname)

    plot_dose_response_dtarget_by_wei(path, bin_size, EXP_GENOTYPE, data)
    plot_dose_response_wei_by_proximity(path, bin_size, EXP_GENOTYPE, data)

    if args.show:
        plt.show()

