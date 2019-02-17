"""
Update node status.
"""
from concurrent.futures import ProcessPoolExecutor
from math import inf
import multiprocessing
import os
import shlex
import subprocess
import time

TIMEOUT = 5  # seconds
MIN_PROCS = 10

PING_CMD = "ansible all -i '{host},' --key-file secret/planetlab.pem \
    -m raw -a 'echo hello' -u ubc_cpen431_1"
SCP_CMD = "scp -i secret/planetlab.pem resources/sonnets.txt \
    ubc_cpen431_1@{host}:~"
RM_CMD = "ansible all -i '{host},' --key-file secret/planetlab.pem \
    -m raw -a 'rm ~/sonnets.txt' -u ubc_cpen431_1"


def __ping(host):
    """Report the time it takes to successfully SSH into a single host.
    If unsucessful or timed out, return None.
    """
    start = time.time()
    try:
        output = subprocess.run(shlex.split(PING_CMD.format(host=host)),
                                check=True, timeout=TIMEOUT)
    except subprocess.SubprocessError:
        return inf

    return time.time() - start


def __get_ping_time(hosts):
    """Report the time to SSH into each host."""
    num_workers = max(multiprocessing.cpu_count(), MIN_PROCS)
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        return dict(zip(hosts, executor.map(__ping, hosts)))


def __scp(host):
    """Report the amount of time it takes to SCP a small file to a host."""
    start = time.time()
    try:
        output = subprocess.run(shlex.split(SCP_CMD.format(host=host)),
                                check=True, timeout=TIMEOUT)
    except subprocess.SubprocessError:
        return inf
    
    return time.time() - start


def __get_transfer_rate(hosts):
    """Report the time to transfer a small (~4KB) file."""
    num_workers = max(multiprocessing.cpu_count() * 2, MIN_PROCS)
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        return dict(zip(hosts, executor.map(__scp, hosts)))


def __rm(host):
    try:
        output = subprocess.run(shlex.split(RM_CMD.format(host=host)),
                                check=True, timeout=TIMEOUT)
    except subprocess.SubprocessError:
        return


def __clean_up(hosts):
    """Delete the files that were previously transferred."""
    num_workers = max(multiprocessing.cpu_count() * 2, MIN_PROCS)
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        executor.map(__rm, hosts)
        return


def get_node_data(nodes):
    """Get node health status for a list of nodes.

    This will measure the time it takes to SSH into a node and time taken to
    copy a small (~4KB) file.

    Args:
        nodes (list): List of nodes to check.
    
    Returns:
        A dictionary with one entry per host. The value is the list of results
        for that host in the order [ping time, transfer time]
    """
    result = {node: [] for node in nodes}

    # test pings & record results
    ping_times = __get_ping_time(nodes)
    for node in nodes:
        result[node].append(ping_times.get(node, inf))

    # test transfer rates
    xfers = [x for x in ping_times if ping_times[x] < TIMEOUT]
    xfer_times = __get_transfer_rate(xfers)
    for node in nodes:
        result[node].append(xfer_times.get(node, inf))

    # delete the file that was transferred
    dirty = [x for x in xfer_times if xfer_times[x] < TIMEOUT]
    __clean_up(dirty)

    return result


if __name__ == "__main__":
    with open(os.path.join("resources", "testnodes.txt")) as f:
        nodes = [x.strip() for x in f.readlines() if not x.isspace()]
        result = get_node_data(nodes)
