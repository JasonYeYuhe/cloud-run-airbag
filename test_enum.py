import os
import sys

# Create a virtualenv, install google-cloud-run, and check the enum
os.system("python3 -m venv venv-test")
os.system("./venv-test/bin/pip install google-cloud-run google-cloud-logging")
os.system('''./venv-test/bin/python3 -c "
from google.cloud import run_v2
print(dir(run_v2.TrafficTargetAllocationType))
"''')
