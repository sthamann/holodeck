"""Prepare an independent native Storefront/Admin browser fixture."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

file = Path(__file__).with_name('capture.py')
spec = importlib.util.spec_from_file_location('scale_capture',file)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)

oracle = capture.Oracle(shipping=625)
base = os.environ['HOLODECK_TEACHER_URL']
domain = oracle.search('sales-channel-domain',{'limit':1})[0]
oracle.sync('sales_channel_domain',[{'id':uuid4().hex,'url':base,'salesChannelId':oracle.channel['id'],
            'languageId':oracle.channel['languageId'],'currencyId':oracle.channel['currencyId'],
            'snippetSetId':domain['snippetSetId']}])
pid = capture.seed_product(oracle,19,'Browser')
code = 'HDUI'+uuid4().hex[:6].upper()
capture.promotion(oracle,code,'percentage',17)
Path(sys.argv[1]).write_text(json.dumps({'product':pid,'code':code}))
