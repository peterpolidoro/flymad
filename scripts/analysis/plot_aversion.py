import json
import math
import os.path
import datetime
import collections
import cPickle
import argparse

import numpy as np
import pandas as pd
import shapely.geometry as sg
import matplotlib.pyplot as plt
import matplotlib.patches

import roslib; roslib.load_manifest('rosbag')
import rosbag

def prepare_data(path):

    dat = json.load(open(path))

    fly_data = dat['data']
    bpath = dat.get('_base',os.path.abspath(os.path.dirname(path)))

    pooled_on = {k:[] for k in "axbh"}
    pooled_off = {k:[] for k in "axbh"}
    pooled_lon = {k:[] for k in "axbh"}

    for exp in fly_data:
        with rosbag.Bag(os.path.join(bpath,exp["bag"])) as bag:

            l_index = []
            l_data = {k:[] for k in ("obj_id","fly_x","fly_y","laser_x","laser_y","laser_power","mode")}
            l_data_names = l_data.keys()

            t_index = []
            t_data = {k:[] for k in ("obj_id","x","y","vx","vy",'v')}
            t_data_names = t_data.keys()

            for topic,msg,rostime in bag.read_messages(topics=["/targeter/targeted",
                                                               "/flymad/tracked",
                                                               "/draw_geom/poly"]):
                if topic == "/targeter/targeted":
                    l_index.append( datetime.datetime.fromtimestamp(msg.header.stamp.to_sec()) )
                    for k in l_data_names:
                        l_data[k].append( getattr(msg,k) )
                elif topic == "/flymad/tracked":
                    if msg.is_living:
                        vx = msg.state_vec[2]
                        vy = msg.state_vec[3]
                        t_index.append( datetime.datetime.fromtimestamp(msg.header.stamp.to_sec()) )
                        t_data['obj_id'].append(msg.obj_id)
                        t_data['x'].append(msg.state_vec[0])
                        t_data['y'].append(msg.state_vec[1])
                        t_data['vx'].append(vx)
                        t_data['vy'].append(vy)
                        t_data['v'].append(math.sqrt( (vx**2) + (vy**2) ))

            l_df = pd.DataFrame(l_data, index=l_index)
            l_df['time'] = l_df.index.values.astype('datetime64[ns]')
            l_df.set_index(['time'], inplace=True)

            #find when the laser was on
            l_on = l_df[l_df['laser_power'] > 0]
            #time of first laser on
            l_on0 = l_df.index[0] + datetime.timedelta(seconds=30)

            t_df = pd.DataFrame(t_data, index=t_index)
            t_df['time'] = t_df.index.values.astype('datetime64[ns]')
            t_df.set_index(['time'], inplace=True)

            #t_off = t_df.head(3000)
            #t_on = t_df.tail(3000)

            #the laser was off at the start and on at the end
            #tracking data when the laser was on 
            t_on = t_df[l_on0:]
            #tracking data when the laser was off
            t_off = t_df[:l_on0]

            print "loading", exp["bag"], len(t_on), "/", len(t_off)

            pooled_on[exp["type"]].append(t_on)
            pooled_off[exp["type"]].append(t_off)
            pooled_lon[exp["type"]].append(l_on)

    cPickle.dump(pooled_on, open(os.path.join(bpath,'pooled_on.pkl'),'wb'), -1)
    cPickle.dump(pooled_off, open(os.path.join(bpath,'pooled_off.pkl'),'wb'), -1)
    cPickle.dump(pooled_lon, open(os.path.join(bpath,'pooled_lon.pkl'),'wb'), -1)

    return pooled_on, pooled_off, pooled_lon

def load_data(path):
    dat = json.load(open(path))
    bpath = dat.get('_base','.')

    return (
        cPickle.load(open(os.path.join(bpath,'pooled_on.pkl'),'rb')),
        cPickle.load(open(os.path.join(bpath,'pooled_off.pkl'),'rb')),
        cPickle.load(open(os.path.join(bpath,'pooled_lon.pkl'),'rb'))
    )

def plot_data(path, data):
    pooled_on, pooled_off, pooled_lon = data

    for k in pooled_on:
        on = pd.concat(pooled_on[k])
        off = pd.concat(pooled_off[k])

        print "%s: on %.2f +/- %.2f off %.2f +/- %.2f" % (
                k, on['v'].mean(), on['v'].std(),
                off['v'].mean(), off['v'].std())

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument('path', nargs=1, help='path to json files')
    parser.add_argument('--only-plot', action='store_true', default=False)
    parser.add_argument('--show', action='store_true', default=False)

    args = parser.parse_args()
    path = args.path[0]

    if args.only_plot:
        data = load_data(path)
    else:
        data = prepare_data(path)

    plot_data(path, data)

    if args.show:
        plt.show()






#             /flymad/tracked            6229 msgs    : flymad/TrackedObj    
#             /targeter/targeted         4838 msgs    : flymad/TargetedObj
