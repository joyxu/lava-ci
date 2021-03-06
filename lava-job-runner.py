#!/usr/bin/python
# <variable> = required
# [variable] = optional
# Usage ./lava-job-runner.py <username> <token> <lava_server_url> [job_repo] [bundle_stream]

import subprocess
import fnmatch
import time
import re
import argparse
from utils import *

job_map = {}


def poll_jobs(connection, timeout):
    run = True
    submitted_jobs = {}
    finished_jobs = {}
    start_time = time.time()

    for job in job_map:
        if job_map[job] is not None:
            # Push
            for job_id in job_map[job]:
                job_name = os.path.basename(job) + '-' + job_id
                submitted_jobs[job_name] = job_id

    while run:
        if timeout < (time.time() - start_time) and timeout != 0:
            print 'Polling timeout reached, collecting completed results'
            run = False
            break
        if not submitted_jobs:
            run = False
            break
        for job in submitted_jobs:
            try:
                status = connection.scheduler.job_status(submitted_jobs[job])
                if status['job_status'] == 'Complete':
                    print 'job-id-' + str(job) + ' : pass'
                    # Pop
                    if status['bundle_sha1']:
                        finished_jobs[submitted_jobs[job]] = {'result': 'PASS', 'bundle': status['bundle_sha1']}
                    else:
                        finished_jobs[submitted_jobs[job]] = {'result': 'PASS', 'bundle': None}
                    submitted_jobs.pop(job, None)
                    break
                elif status['job_status'] == 'Incomplete' or status['job_status'] == 'Canceled' or status['job_status'] == 'Canceling':
                    print 'job-id-' + str(job) + ' : fail'
                    # Pop
                    if status['bundle_sha1']:
                        finished_jobs[submitted_jobs[job]] = {'result': 'FAIL', 'bundle': status['bundle_sha1']}
                    else:
                        finished_jobs[submitted_jobs[job]] = {'result': 'FAIL', 'bundle': None}
                    submitted_jobs.pop(job, None)
                    break
                else:
                    print str(submitted_jobs[job]) + ' - ' + str(status['job_status'])
                    time.sleep(10)
            except (xmlrpclib.ProtocolError, xmlrpclib.Fault, IOError) as e:
                print "POLLING ERROR!"
                print e
                continue

    return finished_jobs


def submit_jobs(connection, server, bundle_stream):
    online_devices, offline_devices = gather_devices(connection)
    online_device_types, offline_device_types = gather_device_types(connection)
    print "Submitting Jobs to Server..."
    for job in job_map:
        try:
            with open(job, 'rb') as stream:
                job_data = stream.read()
                # Injection
                if bundle_stream is not None:
                    job_data = re.sub('LAVA_SERVER', server, job_data)
                    job_data = re.sub('BUNDLE_STREAM', bundle_stream, job_data)
            job_info = json.loads(job_data)
            # Check if request device(s) are available
            if 'target' in job_info:
                if job_info['target'] in offline_devices:
                    print "%s is OFFLINE skipping submission" % job_info['target']
                    print os.path.basename(job) + ': skip'
                elif job_info['target'] in online_devices:
                    pass
                    jobs = connection.scheduler.submit_job(job_data)
                    if isinstance(jobs, int):
                        jobs = str(jobs).split()
                    job_map[job] = jobs
                else:
                    print "No target available on server, skipping..."
                    print os.path.basename(job) + ' : skip'
            elif 'device_type' in job_info:
                if job_info['device_type'] in offline_device_types:
                    print "All device types: %s are OFFLINE, skipping..." % job_info['device_type']
                    print os.path.basename(job) + ' : skip'
                elif job_info['device_type'] in online_device_types:
                    pass
                    jobs = connection.scheduler.submit_job(job_data)
                    if isinstance(jobs, int):
                        jobs = str(jobs).split()
                    job_map[job] = jobs
                else:
                    print "No device-type available on server, skipping..."
                    print os.path.basename(job) + ' : skip'
            elif 'device_group' in job_info:
                print "Multinode Job Detected! Checking if required devices are available..."
                multinode_online_device_types = online_device_types
                server_has_required_devices = True
                for groups in job_info['device_group']:
                    if groups['device_type'] in offline_device_types:
                        print "All device types: %s are OFFLINE, skipping..." % groups['device_type']
                        server_has_required_devices = False
                        print os.path.basename(job) + ' : skip'
                        break
                    elif groups['device_type'] in online_device_types:
                        if groups['count'] > multinode_online_device_types[groups['device_type']]:
                            print "Server does not have enough online devices to submit job!"
                            print os.path.basename(job) + ' : skip'
                            server_has_required_devices = False
                            break
                        elif groups['count'] <= multinode_online_device_types[groups['device_type']]:
                            print "Server has enough devices for this group!"
                            multinode_online_device_types[groups['device_type']] = multinode_online_device_types[groups['device_type']] - groups['count']
                        else:
                            print "Should never get here!"
                            print os.path.basename(job) + ' : skip'
                            server_has_required_devices = False
                            break
                    else:
                        print "No device-type available on server, skipping..."
                        print os.path.basename(job) + ' : skip'
                if server_has_required_devices:
                    print "Submitting Multinode Job!"
                    jobs = connection.scheduler.submit_job(job_data)
                    if isinstance(jobs, int):
                        jobs = str(jobs).split()
                    job_map[job] = jobs
            elif 'vm_group' in job_info:
                print "VMGroup Job Detected! Checking if required devices are available..."
                vmgroup_online_device_types = online_device_types
                server_has_required_devices = True
                for groups in job_info['vm_group']:
                    if job_info['vm_group'][groups]:
                        if 'device_type' in job_info['vm_group'][groups]:
                            if job_info['vm_group'][groups]['device_type'] in offline_device_types:
                                print "All device types: %s are OFFLINE, skipping..." % groups['device_type']
                                server_has_required_devices = False
                                print os.path.basename(job) + ' : skip'
                                break
                            elif job_info['vm_group'][groups]['device_type'] in online_device_types:
                                if 'count' in job_info['vm_group'][groups]:
                                    count = job_info['vm_group'][groups]['device_type']['count']
                                else:
                                    count = 1
                                if count > vmgroup_online_device_types[job_info['vm_group'][groups]['device_type']]:
                                    print "Server does not have enough online devices to submit job!"
                                    print os.path.basename(job) + ' : skip'
                                    server_has_required_devices = False
                                    break
                                elif count <= vmgroup_online_device_types[job_info['vm_group'][groups]['device_type']]:
                                    print "Server has enough devices for this group!"
                                    vmgroup_online_device_types[job_info['vm_group'][groups]['device_type']] = vmgroup_online_device_types[job_info['vm_group'][groups]['device_type']] - count
                                else:
                                    print "Should never get here!"
                                    print os.path.basename(job) + ' : skip'
                                    server_has_required_devices = False
                                    break
                    else:
                            print "No device-type available on server, skipping..."
                            print os.path.basename(job) + ' : skip'
                if server_has_required_devices:
                    print "Submitting VMGroups Job!"
                    jobs = connection.scheduler.submit_job(job_data)
                    if isinstance(jobs, int):
                        jobs = str(jobs).split()
                    job_map[job] = jobs
            else:
                print "Should never get here"
                print os.path.basename(job) + ' : skip'
        except (xmlrpclib.ProtocolError, xmlrpclib.Fault, IOError, ValueError) as e:
            print "JSON VALIDATION ERROR!"
            print job
            print e
            continue


def load_jobs():
    top = os.getcwd()
    for root, dirnames, filenames in os.walk(top):
        for filename in fnmatch.filter(filenames, '*.json'):
            job_map[os.path.join(root, filename)] = None


def retrieve_jobs(jobs):
    cmd = 'git clone %s' % jobs
    try:
        print "Cloning LAVA Jobs..."
        subprocess.check_output(cmd, shell=True)
        print "Clone Successful!"
        print "clone-jobs : pass"
    except subprocess.CalledProcessError as e:
        print "ERROR!"
        print "Unable to clone %s" % jobs
        print "clone-jobs : fail"
        exit(1)


def gather_devices(connection):
    online_devices = {}
    offline_devices = {}
    print "Gathering Devices..."
    all_devices = connection.scheduler.all_devices()
    for device in all_devices:
        if device[2] == 'offline':
            offline_devices[device[0]] = 1
        else:
            online_devices[device[0]] = 1
    print "Gathered Devices Successfully!"
    return online_devices, offline_devices


def gather_device_types(connection):
    online_device_types = {}
    offline_device_types = {}
    print "Gathering Device Types..."
    all_device_types = connection.scheduler.all_device_types()
    for device_type in all_device_types:
        # Only use dictionary data structures
        if isinstance(device_type, dict):
            # Retired
            if device_type['idle'] == 0 and device_type['busy'] == 0 and device_type['offline'] == 0:
                offline_device_types[device_type['name']] = 0
            # Running
            elif device_type['idle'] > 0 or device_type['busy'] > 0:
                online_device_types[device_type['name']] = device_type['idle'] + device_type['busy']
            # Offline
            else:
                offline_device_types[device_type['name']] = device_type['offline']
    print "Gathered Device Types Successfully!"
    return online_device_types, offline_device_types


def main(args):
    url = validate_input(args.username, args.token, args.server)
    connection = connect(url)
    if args.repo:
        retrieve_jobs(args.repo)
    load_jobs()
    start_time = time.time()
    if args.stream:
        submit_jobs(connection, args.server, args.stream)
    else:
        submit_jobs(connection, args.server, bundle_stream=None)
    if args.poll:
        jobs = poll_jobs(connection, args.timeout)
        end_time = time.time()
        if args.bisect:
            for job_id in jobs:
                if 'result' in jobs[job_id]:
                    if jobs[job_id]['result'] == 'FAIL':
                        exit(1)
        jobs['duration'] = end_time - start_time
        jobs['username'] = args.username
        jobs['token'] = args.token
        jobs['server'] = args.server
        results_directory = os.getcwd() + '/results'
        mkdir(results_directory)
        write_json(args.poll, results_directory, jobs)
    exit(0)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("username", help="username for the LAVA server")
    parser.add_argument("token", help="token for LAVA server api")
    parser.add_argument("server", help="server url for LAVA server")
    parser.add_argument("--stream", help="bundle stream for LAVA server")
    parser.add_argument("--repo", help="git repo for LAVA jobs")
    parser.add_argument("--poll", help="poll the submitted LAVA jobs, dumps info into specified json")
    parser.add_argument("--timeout", type=int, default=0, help="polling timeout in seconds. default is 0.")
    parser.add_argument('--bisect', help="bisection mode, returns 1 on any job failures", action='store_true')
    args = parser.parse_args()
    main(args)