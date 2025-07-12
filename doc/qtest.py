import subprocess
import os
import signal
import time
from datetime import datetime
import logging
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import argparse
from pathlib import Path
import numpy as np
import csv

SEPARATE= '====================\n'
TRY = 'Runtime:%s,Turns:%d\n'

def log(_name, path):
    ctime = datetime.now().strftime("%d-%m-%Y_%H-%M-%S");
    end_dir = path.joinpath(_name)
    if end_dir.exists() == False:
        os.makedirs(end_dir.as_posix())
    file_name = end_dir.joinpath(ctime + '.csv')
    return file_name

def parse_test_arg(args, application=None):
    cmd = ""
    port = ""
    if application == 'redis':
        port = "6379"
    elif application == 'nginx':
        port = '8080'
    else:
        port = "11211"
    match args.command:
        case 'wrk':
            dur = args.duration
            conn = args.connections
            thr = args.threads
            cmd = f'./bencht/wrk -d {dur}s -t {thr} -c {conn} http://127.0.0.1:8080/test.html'
        case 'memslap':
            ops = args.ops
            cmd = f'/home/christo/libmemcached/build/src/bin/memslap --servers=127.0.0.1:{port} -t {ops} -c 100'
        case 'memtier':
            clients = args.clients
            threads = args.threads
            ratio = args.ratio
            random = ""
            if args.random:
                random = '-R'
            cmd = f'memtier_benchmark -p {port} -s 127.0.0.1 --protocol={application} {random} '\
             f'--ratio={ratio} -c {clients} -t {threads} --hide-histogram'
        case _:
            print('Invalid test')
            return 1
    return cmd

def perf_from_qlog(log_name, platform, components):
    qlog_path = '/var/log/quark/quark.log'
    #Cleanup previous log
    try:
        os.remove(qlog_path)
    except OSError:
        pass
    line_delim = 'Perf:'
    grep = {
            #TODO: in Quark - {Create, CvmMemoryProtect}/1000 -> all values to us
            'creation': ['Create', 'CvmMemoryProtect',],
            'start-up': ['Boot', 'Attestation', 'StartApp'],
            'attestation':['Attestation', 'TokenAcquisition',
                           'RCAR', 'ReportGeneration'],
            }
    test = 'docker run --rm -d --runtime=quark ubuntu:20.04 bash -c exit'
    for i in range(100):
        result = subprocess.run(test, shell=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        if result.stderr != '':
            print(result)
            print('QPerf: Unexpected output - possible error');
            return 1
    res = {}
    print(components)
    with open(qlog_path, 'r') as log:
        lines = log.readlines()
        for comp in components:
            data = {}
            for line in lines:
                if line_delim in line:
                    cont = line.rstrip('\n').split('-')
                    search = grep.get(comp)
                    print(cont)
                    print(search)
                    for consern in search:
                        if consern in cont[0]:
                            key = cont[0].split()[-1]
                            if consern in data:
                                data[key] += float(cont[-1])
                            else:
                                data.update({key:float(cont[-1])})
            print(data)
            res.update({comp:data})
    print(res)
    for comp in components:
        res_file = log_name.as_posix().split('.')[0] + '_' + platform.rstrip("'") + '_' + comp + '.csv'
        print(res_file)
        header = ['perf', comp]
        data = res.get(comp)
        with open(res_file, 'a', newline='') as f:
            _writer = csv.writer(f)
            _writer.writerow(header)
            row = []
            for k, v in data.items():
                row.append(k)
                row.append((v/float(100))/1000000) #to ms
                _writer.writerow(row)
            print(row)
    time.sleep(5)


def startup_time(log_name, runtimes=["quark"], tries=10):
    rtime = ""
    tmp_file = log_name.as_posix() + '.tmp'
    result = subprocess.run("docker create ubuntu:20.04", shell=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True)
    cid = result.stdout.split('\n')[0]
    with open(tmp_file, 'a+') as f:
        for r in runtimes:
            if r != 'native':
                rtime = "--runtime="+r
            command = f'date +%s%N;docker run --rm {rtime} ubuntu:20.04 /bin/date +%s%N'
            f.write(TRY % (r, tries))
            f.write(SEPARATE)
            for i in range(tries):
                result = subprocess.run(command, shell=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True)
                vals = result.stdout.split('\n')[:-1]
                diff = (float(vals[1]) - float(vals[0])) * 10**(-9)
                f.write("%.9f\n" % diff)
    _adjust_startup_res(tmp_file, log_name)
    subprocess.run(f"docker rm -f {cid}", shell=True, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.STDOUT)
def nginx_ops(log_name, runtimes, tries=100000):
    rtime = ""
    logs = []
    for r in runtimes:
        if r != 'native':
            rtime = "--runtime="+r
        cname = r+'-nginx'
        tmp_file = log_name.as_posix() + '.'+ r + '.tmp'
        command = f'docker run --rm {rtime} -p 80:80 --name {cname} -d nginx'
        check_ready = "ps -e|grep nginx &> /dev/null; echo $?"
        cleanup = f'docker rm -f {cname}'
        check_op = f'ab -n {tries} -c 10 http://localhost/index.html'

        with open(tmp_file, 'a', newline='') as f:
            print(f'Start test for:{cname} - file name:{tmp_file}')
            pid = os.fork()
            if pid == 0:
                subprocess.run(command, shell=True, stdout=subprocess.DEVNULL)
                return 0
            else:
                time.sleep(10)
                subprocess.run(check_op, shell=True, stdout=f,
                               stderr=subprocess.STDOUT, text=True)
                f.flush()
                subprocess.run(cleanup, shell=True, check=True)
                os.wait()
                logs.append(tmp_file)
    if len(logs) > 0:
        _adjust_nginx_res(logs)
    else:
        print("no logs from redis-ops")

def _adjust_nginx_res(logs):
    data = ['GET']
    header = ["test"]
    for f in logs:
        runtime = f.split('.')[-2]
        header.append(runtime)
        with open(f, 'r', newline='') as fd:
            content = fd.readlines()
            print(content)
            for line in content:
                if 'Requests ' in line:
                    rps = line.split()[-3]
                    data.append(rps)
                    break
    print("data:", data)
    log_file = logs[0].split('.')[0] + '.csv'
    with open(log_file, 'a', newline='') as f:
        _writer = csv.writer(f)
        _writer.writerow(header)
        _writer.writerow(data)

def getset_perf(lname, runtimes, image, test_cont, test):
    rtime = ""
    logs = []
    for r in runtimes:
        if r != 'native':
            rtime = "--runtime="+r
        cname = r+image
        tmp_file = lname.as_posix() + '.'+ r + '.tmp'
        command = test_cont.format(rtime, cname)
        print(command)
        cleanup = f'docker rm -f {cname}'

        with open(tmp_file, 'a', newline='') as f:
            print(f'Start test for:{cname} - file name:{tmp_file}\ncommand:{command}\ntest:{test}')
            pid = os.fork()
            if pid == 0:
                subprocess.run(command, shell=True)
                return 0
            else:
                time.sleep(10)
                print(test)
                subprocess.run(test, shell=True, stdout=f,
                               stderr=subprocess.STDOUT, text=True)
                f.flush()
                subprocess.run(cleanup, shell=True, check=True)
                os.wait()
                logs.append(tmp_file)
            print(f'End test for:{cname} - file name:{tmp_file}')
    if len(logs) > 0:
        _adjust_perf_res(logs, test)
    else:
        print("no logs from get/set perf")

def _adjust_perf_res(logs, test):
    _test = test.split()
    ops = []
    clients = 0
    header = ["test"]
    memtier_bin = False
    data_pos = []
    res = {}
    if 'wrk' in _test[0]:
        ops = ["Requests", "Transfer"]
        data_pos = [1]
    elif "memslap" in _test[0]:
        ops[0] = _test[3].upper()
        clients = 100
        data_pos = [8]
    else:
        memtier_bin = True
        ops = ["Get", "Set"]
        clients = [-3]
        data_pos = [1, 8]
    for f in logs:
        runtime = f.split('.')[-2]
        header.append(runtime)
        with open(f, 'r', newline='') as fd:
            content = fd.readlines()
            print(content)
            for line in content:
                for el in ops:
                    if el in line:
                        output = line.split()
                        if 'Req' in el:
                            el = 'Get'
                        print(output)
                        for i in range(len(data_pos)):
                            val = output[data_pos[i]]
                            if 'wrk' in _test[0]:
                                val = ''.join(filter(lambda x: x.isdigit() or x == '.', val))
                                if el == 'Get':
                                    val = float(val) / 1000
                            print(f'val:{val} - pos:{i} - ops:{el}' )
                            if el.upper() not in res:
                                res[el.upper()] = [val]
                            else:
                                res[el.upper()].append(val)
    print("Res:", res)
    return 1
    log_file_name_base = logs[0].split('.')[0]
    log_files = []
    if memtier_bin:
        log_files.append(log_file_name_base + '_ops.csv')
        log_files.append(log_file_name_base + '_bd.csv')
    else:
        log_files.append(log_file_name_base + '.csv')

    for i in range(len(log_files)):
        with open(log_files[i], 'a', newline='') as f:
            _writer = csv.writer(f)
            _writer.writerow(header)
            for k, v in res.items():
                data = []
                data.append(k)
                if memtier_bin:
                    for e in range(len(v)):
                        if i == 0 and e % 2 == 0:
                            data.append(v[e])
                        elif memtier_bini == 1 and e % 2 == 1:
                            data.append(v[e])
                    row = data
                else:
                    row = data + v
                _writer.writerow(row)

def redis_ops(log_name, runtimes, tries=100000):
    rtime = ""
    logs = []
    for r in runtimes:
        if r != 'native':
            rtime = "--runtime="+r
        cname = r+'-redis'
        tmp_file = log_name.as_posix() + '.'+ r + '.tmp'
        command = f'docker run --rm {rtime} -p 6379:6379 -d --name {cname} redis'
        cleanup = f'docker rm -f {cname}'
        check_ready = "ps -e|grep redis &> /dev/null; echo $?"
        check_op = f'redis-benchmark -n {tries} -c 20 --csv'

        with open(tmp_file, 'a', newline='') as f:
            print(f'Start test for:{cname} - file name:{tmp_file}')
            pid = os.fork()
            if pid == 0:
                subprocess.run(command, shell=True)
                return 0
            else:
                time.sleep(10)
                subprocess.run(check_op, shell=True, stdout=f,
                               stderr=subprocess.STDOUT, text=True)
                f.flush()
                subprocess.run(cleanup, shell=True, check=True)
                os.wait()
                logs.append(tmp_file)
            print(f'End test for:{cname} - file name:{tmp_file}')
    if len(logs) > 0:
        _adjust_redis_res(logs)
    else:
        print("no logs from redis-ops")

def _adjust_redis_res(files):
    res = {}
    header = ["test"]
    for f in files:
        runtime = f.split('.')[-2]
        header.append(runtime)
        with open(f, 'r', newline='') as _csv:
            _reader = csv.reader(_csv, delimiter=',')
            for r in _reader:
                if r[0] == 'test':
                    continue
                if r[0] in res:
                    res[r[0]].append(r[1])
                else:
                    res[r[0]] = [r[1]]
    print("res:", res)
    log_file = files[0].split('.')[0] + '.csv'
    with open(log_file, 'a', newline='') as f:
        _writer = csv.writer(f)
        _writer.writerow(header)
        for k, v in res.items():
            data = []
            data.append(k)
            row = data + v
            _writer.writerow(row)

def _adjust_startup_res(src_fd, dest_file):
    data = {}
    with open(src_fd, 'r') as fd:
        content = fd.readlines()
        print(content)
        runtime = ""
        times = int((content[0].split(','))[-1].split(':')[-1].replace('\n', ''))
        for i in range(0, len(content) - times, times + 2):
            head = content[i].split(',')
            runtime = head[0].split(':')[-1]
            sum = 0
            for j in range(0, times):
                sum = sum + float(content[i+2+j].replace('\n', ''))
                data[runtime] = "%.9f" % (sum / times)
        print(data)
        with open(dest_file, 'a', newline='') as _csv:
            header = ['test']
            keys = data.keys()
            for k in keys:
                header.append(k)
            row = ['startup']
            for h in header[1:]:
                row.append(data[h])
            _writer = csv.writer(_csv)
            _writer.writerow(header)
            _writer.writerow(row)

def _ylabel(test):
    #TODO: metric
    match test:
        case 'startup':
            return 'ms'
        case 'redis-ops':
            return 'Req/s'
        case 'nginx-ops':
            return 'Req/s'

def build_plot(file):
    test = file.as_posix().split('/')[-2]
    # Load data from CSV
    data = np.genfromtxt(file, delimiter=',', dtype=None, names=True, encoding=None)
    # Extract data and convert categories to alphabetical letters
    value_columns = data.dtype.names[1:]  # ['native', 'runsc']
    raw_categories = np.atleast_1d(data[data.dtype.names[0]])  # ['startup']
    # Create letter-category mapping
    letter_to_category = {}
    alphabet_letters = [chr(65 + i) for i in range(26)]  # A-Z
    categories = []
    for i, cat in enumerate(raw_categories):
        letter = alphabet_letters[i] if i < 26 else f'Z_{i+1}'
        categories.append(letter)
        letter_to_category[letter] = cat

    values = np.array([np.atleast_1d(data[col]) for col in value_columns])
    # Create plot
    fig, ax = plt.subplots(figsize=(10, 6))
    n_categories = len(categories)
    n_values = len(value_columns)
    width = 0.8 / max(n_values, 1)
    x = np.arange(n_categories)
    # Plot with distinct colors
    colors = plt.cm.tab10(np.linspace(0, 1, n_values))
    for i in range(n_values):
        offset = width * i - width * (n_values - 1) / 2
        bars = ax.bar(x + offset, values[i], width,
                     label=value_columns[i], color=colors[i])
    # Customize axes
    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_xlabel('Categories (Letters)')
    ax.set_ylabel(_ylabel(test))
    ax.set_title(test)
    ax.grid(axis='y', alpha=0.3)
    # Create comprehensive legend
    handles, labels = ax.get_legend_handles_labels()
    # Add letter-category mapping to legend
    mapping_entries = []
    for letter in categories:
        mapping_entries.append(f"{letter} = {letter_to_category[letter]}")
    # Create legend with three parts
    from matplotlib.lines import Line2D
    legend_elements = [
        *[Line2D([0], [0], color=colors[i], lw=4, label=value_columns[i])
         for i in range(n_values)],
        Line2D([0], [0], marker='', color='w', label='\n'.join(mapping_entries))
    ]
    ax.legend(handles=legend_elements,
              title='Legend:\nMetrics & Category Mapping',
              bbox_to_anchor=(1.25, 1),
              loc='upper right')
    plt.tight_layout()
    plt.show()

def main():
    argpars = argparse.ArgumentParser(add_help=False)
    argpars.add_argument('--type', help='Performance measurement or plot collected data',
                         choices=['startup', 'redis-ops', 'nginx-ops', 'memcached-ops',
                                  'quark-rt', 'plot'],
                         default='startup')
    argpars.add_argument('--runtime', help='Select the runtime for tests (not applyed for "plot")',
                         choices=['all', 'native', 'runsc', 'quark'], nargs='+', default=['all'])
    argpars.add_argument('--path', help='All except "plot":Directory to save measurement \
        \nreport Only for "plot":Create a plot from the passed file', type=Path)
    argpars.add_argument('--for', help='(Temporay command) Select the test type to plot',
                         choices=['startup', 'redis'], nargs=1, default='startup')

    subparsers = argpars.add_subparsers(dest='command', metavar={'memtier', 'memslap', 'wrk', 'qperf'})
    bench_qperf = subparsers.add_parser('qperf', add_help=False)
    bench_qperf.add_argument('--platform', type=ascii, choices=['native', 'realm', 'tdx', 'sevsnp'], default='native')
    bench_qperf.add_argument('--component', choices=['attestation', 'creation', 'start-up'],
                             default=['creation'], nargs='+')
    bench_wrk = subparsers.add_parser('wrk', add_help=False)
    bench_wrk.add_argument('--duration', help='Duration of test', type=int, default='60')
    bench_wrk.add_argument('--connections', help='Number of connections (>= threads)', type=int, default='4')
    bench_wrk.add_argument('--threads', help='Number of threads to spawn', type=int, default='4')

    bench_memtier = subparsers.add_parser('memtier', add_help=False)
    bench_memtier.add_argument('--random', action='store_false', help='Randomized data access')
    bench_memtier.add_argument('--ratio', help='specify ops ratio SET:GET', default='1:10')
    bench_memtier.add_argument('--clients', help='specify number of clients', default='100')
    bench_memtier.add_argument('--threads', help='specify number of threads', default='4')

    bench_memslap = subparsers.add_parser('memslap', add_help=False)
    bench_memslap.add_argument('--ops', choices =['get', 'set'], default='get',
                         help='Benchmark (Redis, Memcached) for ops:"GET, SET" for 10000 keys x 100 threads.')

    args = argpars.parse_args()
    print(args)
    cmd_type = args.type
    log_path = args.path
    if cmd_type == 'plot':
        build_plot(log_path)
    else:
        lname = log(cmd_type, log_path)
        runtimes = []
        match args.runtime[0]:
            case 'all':
                runtimes = ['native', 'runsc', 'quark']
            case _:
                runtimes = args.runtime
        try:
            match cmd_type:
                case 'startup':
                    startup_time(lname, runtimes)
                case 'qperf':
                    perf_from_qlog(lname, runtimes)
                case 'redis-ops':
                    if args.command == 'memtier':
                        test = parse_test_arg(args, 'redis')
                        test_cnt = 'docker run --rm {0} -p 6379:6379 --name {1} -d redis'
                        print(test_cnt)
                        getset_perf(lname, runtimes, '-redis', test_cnt, test)
                    else:
                        redis_ops(lname, runtimes)
                case 'nginx-ops':
                    if args.command == 'wrk':
                        test = parse_test_arg(args, 'nginx')
                        test_cnt = 'docker run --rm {0} -d -p 8080:80 --name {1} -v $(realpath .)'\
                        '/becht/http-test-files:/usr/share/nginx/html nginx'
                        getset_perf(lname, runtimes, '-nginx', test_cnt, test)
                    else:
                        nginx_ops(lname, runtimes)
                case 'memcached-ops':
                    test = parse_test_arg(args, 'memcache_text')
                    test_cnt = 'docker run --rm {0} -p 11211:11211 --name {1} -d memcached'
                    getset_perf(lname, runtimes, '-memcached', test_cnt, test)
                case 'quark-rt':
                     perf_from_qlog(lname, args.platform, args.component)
                case _:
                     print(f'Error: command \'cmd_type\' not implemented')
                     return 1
        except Exception as ex:
            logging.exception(ex)
            os.remove(lname)
            return

if __name__ == "__main__":
    main()
