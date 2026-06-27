import os
import sys

os.system('''./venv-test/bin/python3 -c "
from google.cloud import run_v2
print('TrafficTarget accepts type_:', 'type_' in run_v2.TrafficTarget()._pb.DESCRIPTOR.fields_by_name or 'type' in run_v2.TrafficTarget()._pb.DESCRIPTOR.fields_by_name)

import inspect
print(inspect.signature(run_v2.ServicesClient.update_service))
"''')
