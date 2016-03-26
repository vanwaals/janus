#!/usr/bin/env python

# python system modules
import os
import sys
import time
import shutil
import logging
import subprocess
import multiprocessing
import argparse
import traceback
import itertools
import random
from argparse import ArgumentParser
from multiprocessing import Value
from multiprocessing import Lock
import yaml

# third-party python modules 
from tabulate import tabulate

# deptran python modules
sys.path += os.path.abspath(os.path.join(os.path.split(__file__)[0], "./rrr/pylib")),
sys.path += os.path.abspath(os.path.join(os.path.split(__file__)[0], "./deptran")),
from simplerpc import Client
from simplerpc.marshal import Marshal
from deptran.rcc_rpc import ServerControlProxy
from deptran.rcc_rpc import ClientControlProxy


cwd = os.getcwd()
deptran_home, ff = os.path.split(os.path.realpath(__file__))
g_log_dir = deptran_home + "/log"

g_latencies_percentage = [0.5, 0.9, 0.99, 0.999]
g_latencies_header = [str(x * 100) + "% LATENCY" for x in g_latencies_percentage]
g_att_latencies_percentage = [0.5, 0.9, 0.99, 0.999]
g_att_latencies_header = [str(x * 100) + "% ATT_LT" for x in g_att_latencies_percentage]
g_n_try_percentage = [0.5, 0.9, 0.99, 0.999]
g_n_try_header = [str(x * 100) + "% N_TRY" for x in g_n_try_percentage]
g_interest_txn = "NEW ORDER"
g_max_latency = 99999.9
g_max_try = 99999.9

hosts_path_g = ""
hosts_map_g = dict()

class TxnInfo(object):
    def __init__(self, txn_type, txn_name, interest):
        self.txn_type = txn_type
        self.start_txn = 0
        self.pre_start_txn = 0
        self.total_txn = 0
        self.pre_total_txn = 0
        self.total_try = 0
        self.pre_total_try = 0
        self.commit_txn = 0
        self.pre_commit_txn = 0
        self.txn_name = txn_name
        self.max_data = list()
        self.max_interval = 0.0
        self.interest = interest
        self.last_interval_start = 0
        self.this_latencies = []
        self.last_latencies = []
        self.attempt_latencies = []
        self.n_try = []
        self.min_interval = 0.0
        self.update_latency = False

        self.mid_status = 0 # 0: not started, 1: ongoing, 3: end
        self.mid_pre_start_txn = 0
        self.mid_start_txn = 0
        self.mid_pre_total_txn = 0
        self.mid_total_txn = 0
        self.mid_pre_total_try = 0
        self.mid_total_try = 0
        self.mid_pre_commit_txn = 0
        self.mid_commit_txn = 0
        self.mid_time = 0.0
        self.mid_latencies = []
        self.mid_attempt_latencies = []
        self.mid_n_try = []

    def set_mid_status(self):
        self.mid_status += 1

    def clear(self):
        self.start_txn = 0
        self.total_txn = 0
        self.total_try = 0
        self.commit_txn = 0
        self.this_latencies = []
        self.min_interval = g_max_latency
        self.attempt_latencies = []
        self.n_try = []

        if self.mid_status == 0:
            self.mid_pre_start_txn = 0
            self.mid_pre_total_txn = 0
            self.mid_pre_total_try = 0
            self.mid_pre_commit_txn = 0
        elif self.mid_status == 1:
            self.mid_start_txn = 0
            self.mid_total_txn = 0
            self.mid_total_try = 0
            self.mid_commit_txn = 0

    def push_res(self, start_txn, total_txn, total_try, commit_txn, 
            this_latencies, last_latencies, latencies, 
            attempt_latencies, interval_time, n_tried):
        self.start_txn += start_txn
        self.total_txn += total_txn
        self.total_try += total_try
        self.commit_txn += commit_txn
        if self.min_interval > interval_time:
            self.min_interval = interval_time

        if self.mid_status == 0:
            self.mid_pre_start_txn += start_txn
            self.mid_pre_total_txn += total_txn
            self.mid_pre_total_try += total_try
            self.mid_pre_commit_txn += commit_txn
        elif self.mid_status == 1:
            self.mid_latencies.extend(latencies)
            self.mid_attempt_latencies.extend(attempt_latencies)
            self.mid_time += interval_time
            self.mid_n_try.extend(n_tried)
            self.mid_start_txn += start_txn
            self.mid_total_txn += total_txn
            self.mid_total_try += total_try
            self.mid_commit_txn += commit_txn

    def get_res(self, interval_time, total_time, set_max, 
            all_total_commits, all_interval_commits, do_sample, do_sample_lock):
        min_latency = g_max_latency
        max_latency = g_max_latency
        latencies_size = len(self.last_latencies)
        interval_latencies = [g_max_latency for x in g_att_latencies_percentage]
        interval_attempt_latencies = [g_max_latency for x in g_att_latencies_percentage]
        int_n_try = [g_max_try for x in g_n_try_percentage]

        interval_tps = int(round((self.commit_txn - self.pre_commit_txn) / interval_time))

        interval_commits = self.commit_txn - self.pre_commit_txn

        if all_total_commits > 0:
            total_ret = [str(round(self.commit_txn * 100.0 / all_total_commits, 2)) + "%", self.txn_name, self.start_txn, self.total_txn, self.total_try, self.commit_txn, int(round(self.commit_txn / total_time))]
        else:
            total_ret = ["----", self.txn_name, self.start_txn, self.total_txn, self.total_try, self.commit_txn, int(round(self.commit_txn / total_time))]

        if all_interval_commits > 0:
            interval_ret = [str(round(interval_commits * 100.0 / all_interval_commits, 2)) + "%", self.txn_name, self.start_txn - self.pre_start_txn, self.total_txn - self.pre_total_txn, self.total_try - self.pre_total_try, interval_commits, interval_tps, min_latency, max_latency]
        else:
            interval_ret = ["----", self.txn_name, self.start_txn - self.pre_start_txn, self.total_txn - self.pre_total_txn, self.total_try - self.pre_total_try, interval_commits, interval_tps, min_latency, max_latency]

        interval_ret.extend(interval_latencies)
        interval_ret.extend(interval_attempt_latencies)
        interval_ret.extend(int_n_try)
        ret = [total_ret, interval_ret]

        if (self.update_latency):
            self.update_latency = False
            ul_index = 9
            while ul_index < len(ret[1]):
                self.max_data[ul_index] = ret[1][ul_index]
                ul_index += 1

        if (set_max):
            if (len(self.max_data) == 0 or self.max_data[6] < interval_tps):
                self.max_data = ret[1]
                self.max_interval = interval_time
                self.update_latency = True

        self.last_interval_start = self.start_txn - self.pre_start_txn
        self.pre_start_txn = self.start_txn
        self.pre_total_txn = self.total_txn
        self.pre_total_try = self.total_try
        self.pre_commit_txn = self.commit_txn
        self.this_latencies = []
        return ret

    def print_mid(self, num_clients):
        start_txn = str(self.mid_start_txn - self.mid_pre_start_txn)
        total_txn = str(self.mid_total_txn - self.mid_pre_total_txn)
        tries = str(self.mid_total_try - self.mid_pre_total_try)
        commit_txn = str(self.mid_commit_txn - self.mid_pre_commit_txn)
        self.mid_time /= num_clients
        tps = str(int(round((self.mid_commit_txn - self.mid_pre_commit_txn) / self.mid_time)))

        self.mid_latencies.sort()
        self.mid_attempt_latencies.sort()
        self.mid_n_try.sort()

        min_latency = g_max_latency
        max_latency = g_max_latency
        latency_str = ""
        latencies_size = len(self.mid_latencies)
        if (latencies_size > 0):
            min_latency = self.mid_latencies[0]
            max_latency = self.mid_latencies[latencies_size - 1]
        latencies_sample_size = [int(x * latencies_size) for x in g_latencies_percentage]
        i = 0
        while i < len(g_latencies_header):
            latency_str += "; " + g_latencies_header[i] + ": "
            s_size = latencies_sample_size[i]
            if s_size != 0:
                latency_str += str(sum(self.mid_latencies[0:s_size]) / s_size)
            else:
                latency_str += str(g_max_latency)
            i += 1

        attempt_latencies_size = len(self.mid_attempt_latencies)
        attempt_latencies_sample_size = [int(x * attempt_latencies_size) for x in g_att_latencies_percentage]
        i = 0
        while i < len(g_att_latencies_header):
            latency_str += "; " + g_att_latencies_header[i] + ": "
            s_size = attempt_latencies_sample_size[i]
            if s_size != 0:
                latency_str += str(sum(self.mid_attempt_latencies[0:s_size]) / s_size)
            else:
                latency_str += str(g_max_latency)
            i += 1

        n_tried_str = ""
        n_try_size = len(self.mid_n_try)
        n_try_sample_size = [int(x * n_try_size) for x in g_n_try_percentage]
        i = 0
        while i < len(g_n_try_header):
            n_tried_str += "; " + g_n_try_header[i] + ": "
            s_size = n_try_sample_size[i]
            if s_size != 0:
                n_tried_str += str(sum(self.mid_n_try[0:s_size]) * 1.0 / s_size)
            else:
                n_tried_str += str(g_max_try)
            i += 1

        print "RECORDING_RESULT: TXN: <" + self.txn_name + ">; STARTED_TXNS: " + start_txn + "; FINISHED_TXNS: " + total_txn + "; ATTEMPTS: " + tries + "; COMMITS: " + commit_txn + "; TPS: " + tps + latency_str + "; TIME: " + str(self.mid_time) + "; LATENCY MIN: " + str(min_latency) + "; LATENCY MAX: " + str(max_latency) + n_tried_str

    def print_max(self):
        latency_str = ""
        i = 0
        for l_str in g_latencies_header:
            latency_str += "; " + l_str + ": " + str(self.max_data[9 + i])
            i += 1

        i = 0
        latency_size = len(g_latencies_header)
        for l_str in g_att_latencies_header:
            latency_str += "; " + l_str + ": " + str(self.max_data[9 + latency_size + i])
            i += 1

        n_tried_str = ""
        i = 0
        att_latency_size = len(g_att_latencies_header)
        for l_str in g_n_try_header:
            n_tried_str += "; " + l_str + ": " + str(self.max_data[9 + latency_size + att_latency_size + i])
            i += 1

        print "RECORDING_RESULT: TXN: <" + str(self.max_data[1]) + ">; STARTED_TXNS: " + str(self.max_data[2]) + "; FINISHED_TXNS: " + str(self.max_data[3]) + "; ATTEMPTS: " + str(self.max_data[4]) + "; COMMITS: " + str(self.max_data[5]) + "; TPS: " + str(self.max_data[6]) + latency_str + "; TIME: " + str(self.max_interval) + "; LATENCY MIN: " + str(self.max_data[7]) + "; LATENCY MAX: " + str(self.max_data[8]) + n_tried_str

class ClientController(object):
    def __init__(self, config, process_infos):
        self.config = config
        self.process_infos = process_infos
        self.benchmark = config['bench']['workload'] 
        self.timeout = config['args'].c_timeout
        self.duration = config['args'].c_duration
        self.taskset = config['args'].c_taskset
        self.log_dir = config['args'].log_dir
        self.interest_txn = config['args'].interest_txn
        self.recording_path = config['args'].recording_path

        self.max_data = list()
        self.finish_set = set()
        self.txn_infos = dict()
        self.rpc_proxy = dict()
        self.txn_names = dict()
        self.machine_n_cores = dict()

        self.start_time = 0
        self.pre_start_txn = 0
        self.start_txn = 0
        self.pre_total_txn = 0
        self.total_txn = 0
        self.pre_total_try = 0
        self.total_try = 0
        self.pre_commit_txn = 0
        self.commit_txn = 0
        self.run_sec = 0
        self.pre_run_sec = 0
        self.run_nsec = 0
        self.pre_run_nsec = 0
        self.n_asking = 0
        self.max_tps = 0

        self.recording_period = False
        self.print_max = False

    def client_run(self, do_sample, do_sample_lock):
        sites = ProcessInfo.get_sites(self.process_infos, 
                                      SiteInfo.SiteType.Client)
        for site in sites:
            site.connect_rpc(self.timeout)
            logging.info("Connected to client site %s @ %s", site.name, site.process.host_address)

        barriers = []
        for site in sites:
            barriers.append(site.process.client_rpc_proxy.async_client_ready_block())
        
        for barrier in barriers:
            barrier.wait()
        logging.info("Clients all ready")

        res = sites[0].process.client_rpc_proxy.sync_client_get_txn_names()
        for k, v in res.items():
            logging.debug("txn: %s - %s", v, k)
            self.txn_names[k] = v

        self.start_client()
        logging.info("Clients started")

        self.benchmark_record(do_sample, do_sample_lock)
        print "Benchmark finished\n"

    def start_client(self):
        sites = ProcessInfo.get_sites(self.process_infos, 
                    SiteInfo.SiteType.Client)
        client_rpc = set()
        for site in sites:
            client_rpc.add(site.process.client_rpc_proxy)
        
        futures = []
        for rpc_proxy in client_rpc:
            futures.append(rpc_proxy.async_client_start())

        for future in futures:
            future.wait()

        logging.info("client start send successfully.")

        self.start_time = time.time()

    def benchmark_record(self, do_sample, do_sample_lock):
        sites = ProcessInfo.get_sites(self.process_infos, 
                                      SiteInfo.SiteType.Client)
        rpc_proxy = set()
        for site in sites:
            rpc_proxy.add(site.process.client_rpc_proxy)
        rpc_proxy = list(rpc_proxy)
    
        while (len(rpc_proxy) != len(self.finish_set)):
            time.sleep(self.timeout)
            logging.info("top client heartbeat; sleep {}".format(self.timeout))
            for k in self.txn_infos.keys():
                self.txn_infos[k].clear()
            self.start_txn = 0
            self.total_txn = 0
            self.total_try = 0
            self.commit_txn = 0
            self.run_sec = 0
            self.run_nsec = 0
        
        futures = []
        for proxy in rpc_proxy:
                try:
                    future = proxy.async_client_response()
                    futures.append(future)
                except:
                    traceback.print_exc()
         
        for future in futures:
                res = future.result
                period_time = res.period_sec + res.period_nsec / 1000000000.0
                for txn_type in res.txn_info.keys():
                    if txn_type not in self.txn_infos:
                        self.txn_infos[txn_type] = TxnInfo(txn_type, self.txn_names[txn_type], self.txn_names[txn_type] == self.interest_txn)
                    self.start_txn += res.txn_info[txn_type].start_txn
                    self.total_txn += res.txn_info[txn_type].total_txn
                    self.total_try += res.txn_info[txn_type].total_try
                    self.commit_txn += res.txn_info[txn_type].commit_txn
                    self.txn_infos[txn_type].push_res(res.txn_info[txn_type].start_txn, res.txn_info[txn_type].total_txn, res.txn_info[txn_type].total_try, res.txn_info[txn_type].commit_txn, res.txn_info[txn_type].this_latency, res.txn_info[txn_type].last_latency, res.txn_info[txn_type].interval_latency, res.txn_info[txn_type].attempt_latency, period_time, res.txn_info[txn_type].num_try)
                self.run_sec += res.run_sec
                self.run_nsec += res.run_nsec
                self.n_asking += res.n_asking
                if (res.is_finish == 1):
                    self.finish_set.add(res)
                self.cur_time = time.time()
                need_break = self.print_stage_result(do_sample, do_sample_lock)
            if (need_break):
                break
            else:
                time.sleep(self.timeout)

    def print_stage_result(self, do_sample, do_sample_lock):
        sites = ProcessInfo.get_sites(self.process_infos, 
                                      SiteInfo.SiteType.Client)

        interval_time = (self.run_sec - self.pre_run_sec \
                        + (self.run_nsec - self.pre_run_nsec) / 1000000000.0) \
                        / len(sites)
        total_time = (self.run_sec + self.run_nsec / 1000000000.0) / len(sites)
        progress = int(round(100 * total_time / self.duration))

        if (self.print_max):
            self.print_max = False
            for k, v in self.txn_infos.items():
                #v.print_max()
                v.print_mid(len(sites))

        if (not self.recording_period):
            if (progress >= 20 and progress <= 60):
                self.recording_period = True
                do_sample_lock.acquire()
                do_sample.value = 1
                do_sample_lock.release()
                for k, v in self.txn_infos.items():
                    v.set_mid_status()
        else:
            if (progress >= 60):
                self.recording_period = False
                self.print_max = True
                do_sample_lock.acquire()
                do_sample.value = 1
                do_sample_lock.release()
                for k, v in self.txn_infos.items():
                    v.set_mid_status()
        output_str = "\nProgress: " + str(progress) + "%\n"
        total_table = []
        interval_table = []
        interval_commits = self.commit_txn - self.pre_commit_txn
        for txn_type in self.txn_infos.keys():
            rows = self.txn_infos[txn_type].get_res(interval_time, total_time, self.recording_period, self.commit_txn, interval_commits, do_sample, do_sample_lock)
            total_table.append(rows[0])
            interval_table.append(rows[1])
        logging.info("total_time: {}".format(total_time))
        total_table.append(["----", "Total", self.start_txn, self.total_txn, self.total_try, self.commit_txn, int(round(self.commit_txn / total_time))])
        interval_total_row = ["----", "Total", self.start_txn - self.pre_start_txn, self.total_txn - self.pre_total_txn, self.total_try - self.pre_total_try, interval_commits, int(round((self.commit_txn - self.pre_commit_txn) / interval_time))]
        interval_total_row.extend([0.0 for x in g_latencies_header])
        interval_total_row.extend([0.0 for x in g_att_latencies_header])
        interval_table.append(interval_total_row)
        total_header = ["RATIO", "NAME", "start", "finish", "attempts", "commits", "TPS"]
        interval_header = ["RATIO", "NAME", "start", "finish", "attempts", "commits", "TPS", "min lt", "max lt"]
        interval_header.extend(g_latencies_header)
        interval_header.extend(g_att_latencies_header)
        interval_header.extend(g_n_try_header)
        output_str += "TOTAL: elapsed time: " + str(round(total_time, 2)) + "\n"
        output_str += tabulate(total_table, headers=total_header) + "\n\n"
        output_str += "INTERVAL: elapsed time: " + str(round(interval_time, 2)) + "\n"
        output_str += tabulate(interval_table, headers=interval_header) + "\n"
        output_str += "\tTotal asking finish: " + str(self.n_asking) + "\n"
        output_str += "----------------------------------------------------------------------\n"
        print output_str

        self.pre_start_txn = self.start_txn
        self.pre_total_txn = self.total_txn
        self.pre_total_try = self.total_try
        self.pre_commit_txn = self.commit_txn
        self.pre_run_sec = self.run_sec
        self.pre_run_nsec = self.run_nsec

        if (self.cur_time - self.start_time > 1.5 * self.duration):
            if (self.print_max):
                self.print_max = False
                for k, v in self.txn_infos.items():
                    v.print_mid(len(sites))
            return True
        else:
            return False

    def client_kill(self):
        logging.info("killing clients ...")
        sites = ProcessInfo.get_sites(self.process_infos, SiteInfo.SiteType.Client)
        hosts = { s.process.host_address for s in sites }
        for host in hosts:
            cmd = "killall deptran_server"
            subprocess.call(['ssh', '-f', host, cmd])

    def client_shutdown(self):
        print "Shutting down clients ..."
        sites = ProcessInfo.get_sites(self.process_infos, SiteInfo.SiteType.Client)
        for site in self.sites:
            try:
                site.rpc_proxy.sync_client_shutdown()
            except:
                traceback.print_exc()

class ServerResponse(object):
    def __init__(self, value_times_pair):
        self.value = value_times_pair.value
        self.times = value_times_pair.times

    def add_one(self, value_times_pair):
        self.value += value_times_pair.value
        self.times += value_times_pair.times

    def get_value(self):
        return self.value

    def get_times(self):
        return self.times

    def get_ave(self):
        if self.times == 0:
            return 0.0
        else:
            return 1.0 * self.value / self.times

class ServerController(object):
    def __init__(self, config, process_infos):
        self.config = config
        self.timeout = config['args'].s_timeout
        self.log_dir = config['args'].log_dir
        taskset = config['args'].s_taskset
        self.recording_path = config['args'].recording_path
        self.process_infos = process_infos
        self.rpc_proxy = dict()
        self.server_kill()

        if (taskset == 1):
            # set task on CPU 1
            self.taskset_func = lambda x: "taskset -c " + str(2 * x + 16)
            logging.info("Setting servers on CPU 1")
        elif (taskset == 2): 
            # set task on CPU 0, odd number cores, no overlapping with irq cores
            self.taskset_func = lambda x: "taskset -c " + str(2 * x + 1)
            logging.info("Setting servers on CPU 0, odd number cores")
        elif (taskset == 3): 
            # set task on CPU 0, even number cores, overlapping with irq cores
            self.taskset_func = lambda x: "taskset -c " + str(2 * x)
            logging.info("Setting servers on CPU 0, even number cores")
        else:
            self.taskset_func = lambda x: ""
            logging.info("No taskset, auto scheduling")
        self.pre_statistics = dict()
        self.pre_time = time.time()

    def server_kill(self):
        hosts = { pi.host_address for pi in self.process_infos.itervalues() }
        logging.info("killing servers on %s", ', '.join(hosts))
        for host in hosts:
            cmd = "killall deptran_server"
            subprocess.call(['ssh', '-f', host, cmd])
    
    def setup_heartbeat(self, client_controller):
        cond = multiprocessing.Condition()
        s_init_finish = Value('i', 0)

        do_sample = Value('i', 0)
        do_sample_lock = Lock()

        server_process = multiprocessing.Process(
                target=self.server_heart_beat, 
                args=(cond, s_init_finish, do_sample, do_sample_lock))
        server_process.daemon = False
        server_process.start()

        logging.info("Waiting for server init ...")
        cond.acquire()
        while (s_init_finish.value == 0):
            cond.wait()
        if s_init_finish.value == 5:
            logging.error("Waiting for server init ... FAIL")
            return None 
        cond.release()
        logging.info("Waiting for server init ... Done")
        
        # let all clients start running the benchmark
        client_controller.client_run(do_sample, do_sample_lock)
        cond.acquire()
        s_init_finish.value = 0
        cond.release()
        return server_process
   
    def shutdown_sites(self, sites):
        for site in sites:
            try:
                site.rpc_proxy.sync_server_shutdown()
            except:
                traceback.print_exc()


    def server_heart_beat(self, cond, s_init_finish, do_sample, do_sample_lock):
        sites = []
        try:
            sites = ProcessInfo.get_sites(self.process_infos,
                                          SiteInfo.SiteType.Server)
            for site in sites:
                site.connect_rpc(self.timeout)
                logging.info("Connected to site %s @ %s", site.name, site.process.host_address)

            for site in sites:
                while (site.rpc_proxy.sync_server_ready() != 1):
                    time.sleep(1) # waiting for server to initialize
                logging.info("site %s ready", site.name)

            cond.acquire()
            s_init_finish.value = 1
            cond.notify()
            cond.release()

            avg_r_cnt = 0.0
            avg_r_sz = 0.0
            avg_cpu_util = 0.0
            sample_result = []
            while (True):
                logging.debug("top server heartbeat loop")
                do_statistics = False
                do_sample_lock.acquire()
                if do_sample.value == 1:
                    do_statistics = True
                    do_sample.value = 0
                do_sample_lock.release()
                i = 0
                r_cnt_sum = 0
                r_cnt_num = 0
                r_sz_sum = 0
                r_sz_num = 0
                statistics = dict()
                cpu_util = [0.0] * len(sites)
                futures = []
                
                for site in sites:
                    logging.debug("ping %s", site.name)
                    if do_statistics:
                        futures.append(site.rpc_proxy.async_server_heart_beat_with_data())
                    else:
                        futures.append(site.rpc_proxy.async_server_heart_beat())

                i = 0
                while (i < len(futures)):
                    if do_statistics:
                        ret = futures[i].result
                        r_cnt_sum += ret.r_cnt_sum
                        r_cnt_num += ret.r_cnt_num
                        r_sz_sum += ret.r_sz_sum
                        r_sz_num += ret.r_sz_num
                        cpu_util[i] = ret.cpu_util
                        for k, v in ret.statistics.items():
                            if k not in statistics:
                                statistics[k] = ServerResponse(v)
                            else:
                                statistics[k].add_one(v)
                    else:
                        futures[i].wait()
                    i += 1
                if do_statistics:
                    total_result = []
                    interval_result = []
                    cur_time = time.time()
                    interval_time = cur_time - self.pre_time
                    self.pre_time = cur_time
                    for k, v in statistics.items():
                        total_result.append([k, v.get_value(), v.get_times(), v.get_ave()])
                        interval_result.append([k, v.get_value(), v.get_times(), v.get_ave(), interval_time])
                    self.pre_statistics = statistics
                    sample_result = interval_result
                    avg_cpu_util = sum(cpu_util) / len(cpu_util)
                    if r_cnt_num != 0:
                        avg_r_cnt = (1.0 * r_cnt_sum) / r_cnt_num
                    else:
                        avg_r_cnt = -1.0
                    if r_sz_num != 0:
                        avg_r_sz = (1.0 * r_sz_sum) / r_sz_num
                    else:
                        avg_r_sz = -1.0
                cond.acquire()
                if (s_init_finish.value == 0):
                    cond.release()
                    break
                cond.release()
                time.sleep(self.timeout / 4)

            for single_record in sample_result:
                print "SERVREC: " + str(single_record[0]) + ": VALUE: " + str(single_record[1]) + "; TIMES: " + str(single_record[2]) + "; MEAN: " + str(single_record[3]) + "; TIME: " + str(single_record[4])
            print "CPUINFO: " + str(avg_cpu_util) + ";"
            print "AVG_LOG_FLUSH_CNT: " + str(avg_r_cnt) + ";"
            print "AVG_LOG_FLUSH_SZ: " + str(avg_r_sz) + ";"
            print "BENCHMARK_SUCCEED"
        except:
            traceback.print_exc()
            cond.acquire()
            s_init_finish.value = 5
            cond.notify()
            cond.release()
    
    def gen_process_cmd(self, process, host_process_counts):
        cmd = []
        cmd.append("cd " + deptran_home + "; ")
        cmd.append("mkdir -p " + self.log_dir + "; ")
        if (len(self.recording_path) != 0):
            recording = " -r '" + self.recording_path + "/deptran_server_" + process.name + "' "
            cmd.append("mkdir -p " + self.recording_path + "; ")
        else:
            recording = ""
        
        s = "nohup " + self.taskset_func(host_process_counts[process.host_address]) + \
               " ./build/deptran_server " + \
               "-b " + \
               "-d " + str(self.config['args'].c_duration) + " " + \
               "-f '" + self.config['args'].config_file.name + "' " + \
               "-P '" + process.name + "' " + \
               "-p " + str(self.config['args'].rpc_port + process.id) + " " \
               "-t " + str(self.config['args'].s_timeout) + " " \
               "-r '" + self.config['args'].log_dir + "' " + \
               recording + \
               "1>'" + self.log_dir + "/proc-" + process.name + ".log' " + \
               "2>'" + self.log_dir + "/proc-" + process.name + ".err' " + \
               "&"

        host_process_counts[process.host_address] += 1
        cmd.append(s)
        return ' '.join(cmd)

    def start(self):
        # this current starts all the processes
        # todo: separate this into a class that starts and stops deptran
        logging.debug(self.process_infos)

        host_process_counts = { host_address: 0 for host_address in self.config['host'].itervalues() }

        for process_name, process in self.process_infos.iteritems():
            logging.info("starting %s @ %s", process_name, process.host_address)
            cmd = self.gen_process_cmd(process, host_process_counts)
            logging.debug("%s", cmd)
            subprocess.call(['ssh', '-f',process.host_address, cmd])

def create_parser():
    
    parser = ArgumentParser()

    parser.add_argument("-f", "--file", dest="config_file", 
            help="read config from FILE, default is sample.yml", 
            default="./config/sample.yml", metavar="FILE", 
            type=argparse.FileType('r'))

    parser.add_argument("-P", "--port", dest="rpc_port", help="port to use", 
            default=5555, metavar="PORT")

    parser.add_argument("-t", "--server-timeout", dest="s_timeout", 
            help="server heart beat timeout in seconds", default=10, 
            action="store", metavar="TIMEOUT", type=int)

    parser.add_argument("-i", "--status-time-interval", dest="c_timeout", 
            help="time interval to report benchmark status in seconds", 
            default=5, action="store", metavar="TIME", type=int)

    parser.add_argument("-d", "--duration", dest="c_duration", 
            help="benchmark running duration in seconds", default=60, 
            action="store", metavar="TIME", type=int)

    parser.add_argument("-S", "--single-server", dest="c_single_server", 
            help="control each client always touch the same server "
                 "0, disabled; 1, each thread will touch a single server; "
                 "2, each process will touch a single server", 
            default=0, action="store", metavar="[0|1|2]")

    parser.add_argument("-T", "--taskset-schema", dest="s_taskset", 
            help="Choose which core to run each server on. "
                 "0: auto; "
                 "1: CPU 1; "
                 "2: CPU 0, odd cores; "
                 "3: CPU 0, even cores;", 
            default=0, action="store", metavar="[0|1|2|3]")

    parser.add_argument("-c", "--client-taskset", dest="c_taskset", 
            help="taskset client processes round robin", default=False, 
            action="store_true")

    parser.add_argument("-l", "--log-dir", dest="log_dir", 
            help="Log file directory", default=g_log_dir, 
            metavar="LOG_DIR")

    parser.add_argument("-r", "--recording-path", dest="recording_path", 
            help="Recording path", default="", metavar="RECORDING_PATH")

    parser.add_argument("-x", "--interest-txn", dest="interest_txn", 
            help="interest txn", default=g_interest_txn, 
            metavar="INTEREST_TXN")

    parser.add_argument("-H", "--hosts", dest="hosts_path", 
            help="hosts path", default="./config/hosts-local", 
            metavar="HOSTS_PATH")
    logging.debug(parser) 
    return parser
    
class TrialConfig:
    def __init__(self, options):
        self.s_timeout = int(options.s_timeout)
        self.c_timeout = int(options.c_timeout)
        self.c_duration = int(options.c_duration)
        self.c_single_server = int(options.c_single_server)
        self.s_taskset = int(options.s_taskset)
        self.c_taskset = options.c_taskset
        self.config_path = os.path.realpath(options.config_path)
        self.hosts_path = os.path.realpath(options.hosts_path)
        self.recording_path = os.path.realpath(options.recording_path)
        self.log_path = os.path.realpath(options.log_dir)
        self.c_interest_txn = str(options.interest_txn)
        self.rpc_port = int(options.rpc_port)        
        
        pass
        
    def check_correctness(self):
        if self.c_single_server not in [0, 1, 2]:
            logging.error("Invalid single server argument.")
            return False
            
        if not os.path.exists(self.config_path):
            logging.error("Config path incorrect.")
            return False
        
        if not os.path.exists(self.hosts_path):
            logging.error("Hosts path incorrect.")
            return False
        
        return True

class SiteInfo:
    class SiteType:
        Client = 1
        Server = 2

    CTRL_PORT_DELTA = 10000
    id = -1

    @staticmethod
    def next_id():
        SiteInfo.id += 1
        return SiteInfo.id

    def __init__(self, process, site_name, site_type, port):
        self.id = SiteInfo.next_id()
        self.process = process
        self.name = site_name 
        if type(site_type) == str:
            if site_type == 'client':
                self.site_type = SiteInfo.SiteType.Client
            else:
                self.site_type = SiteInfo.SiteType.Server
        else:
            self.site_type = site_type
        
        if site_type == 'client':
            self.port = int(port)
            self.rpc_port = int(port)
        elif port is not None:
            self.port = int(port)
            self.rpc_port = self.port + self.CTRL_PORT_DELTA
        else:
            logging.error("server definition should have a port")
            sys.exit(1) 


    def connect_rpc(self, timeout):
        if self.site_type == SiteInfo.SiteType.Client:
            if self.process.client_rpc_proxy is not None:
                logging.info("client control rpc already connected for site %s",
                             self.name)
                self.rpc_proxy = self.process.client_rpc_proxy
                return True
            logging.info("start connect to client ctrl rpc for site %s @ %s:%s", 
                     self.name, 
                     self.process.host_address, 
                     self.process.rpc_port)
            port = self.process.rpc_port
        else:
            logging.info("start connect to server ctrl rpc for site %s @ %s:%s", 
                     self.name, 
                     self.process.host_address, 
                     self.rpc_port)
            port = self.rpc_port

        connect_start = time.time()
        self.rpc_client = Client()
        result = None 
        while (result != 0):
            bind_address = "{host}:{port}".format(
                host=self.process.host_address,
                port=port)
            result = self.rpc_client.connect(bind_address)
            if time.time() - connect_start > timeout:
                raise RuntimeError("rpc connect time out")
            time.sleep(0.1)

        if self.site_type == SiteInfo.SiteType.Client:
            self.process.client_rpc_proxy = ClientControlProxy(self.rpc_client)
            self.rpc_proxy = self.process.client_rpc_proxy
        else:
            self.rpc_proxy = ServerControlProxy(self.rpc_client)
    return True

class ProcessInfo:
    id = -1

    def __init__(self, name, address, rpc_port):
        self.id = ProcessInfo.next_id()
        self.name = name
        self.host_address = address
        self.rpc_port = rpc_port + self.id
        self.client_rpc_proxy = None
        self.sites = []

    def add_site(self, site_name, site_type, port):
        obj = SiteInfo(self, site_name, site_type, port)
        self.sites.append(obj)
        return obj

    def client_sites(self):
        return [ site for site in self.sites if site.site_type ==
                SiteInfo.SiteType.Client ]

    def server_sites(self):
        return [ site for site in self.sites if site.site_type ==
                SiteInfo.SiteType.Server ]
    
    @staticmethod
    def next_id():
        ProcessInfo.id += 1
        return ProcessInfo.id

    @staticmethod
    def get_sites(process_list, site_type):
        sites = []
        
        if site_type == SiteInfo.SiteType.Client:
            m = ProcessInfo.client_sites
        else:
            m = ProcessInfo.server_sites

        for process in process_list.itervalues():
            for site in m(process):
                sites.append(site)
        return sites


def get_process_info(config):
    hosts = config['host']
    processes = config['process']
    sites = config['site']

    process_infos = { process_name: ProcessInfo(process_name,
                                                hosts[process_name],
                                                config['args'].rpc_port)
                     for (_, process_name) in processes.iteritems() }
    for site_type in ['server', 'client']:
        for site in itertools.chain(*sites[site_type]):
            if ':' in site:
                site, port = site.split(':')
            else:
                port = int(config['args'].rpc_port) 
            pi = process_infos[processes[site]]
            pi.add_site(site, site_type, port)
    return process_infos

def main():
    logging.basicConfig(format='%(asctime)s: %(levelname)s: %(message)s', level=logging.DEBUG)
    server_controller = None
    client_controller = None
    config = None

    try:
        options = create_parser().parse_args()
        config = yaml.load(options.config_file)
        config['args'] = options 
        
        process_infos = get_process_info(config)
        server_controller = ServerController(config, process_infos)
        server_controller.start()

        client_controller = ClientController(config, process_infos)
        process = server_controller.setup_heartbeat(client_controller)
        if process is not None:
            process.join()
        
    except Exception:
        traceback.print_exc()
    finally:
        logging.info("shutting down...")
        if server_controller is not None:
            server_controller.server_kill()
        if client_controller is not None:
            client_controller.client_kill()
        config['args'].config_file.close()

if __name__ == "__main__":
    main()
