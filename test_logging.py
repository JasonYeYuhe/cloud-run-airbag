import os
import sys

os.system('''./venv-test/bin/python3 -c "
from google.cloud import logging
import inspect
print(inspect.signature(logging.Client.list_entries))
"''')
