# run in debug mode?
DEBUG = False

# the nodes to keep track of
NODE_FILE = "allnodes.txt"

# the AWS port number
AWS_PORT = 60001

# secret key for heartbeats
SECRET = "foo"

# node tracker settings
WAKE_DEAD = False  # whether to run the wakeup thread at all
WAKE_TIMEOUT = 300  # max time to spend waking up a thread
PRUNE_INTERVAL = 15 * 60  # how often (s) to run pruner
PRUNE_THRESHOLD = 30  # threshold (min) to consider a node dead
SCP_TIMEOUT = 20  # max time to spend SCPing the test file
SCP_DECAY = 0.4  # decay factor for SCP time estimates
