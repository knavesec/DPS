#!/usr/bin/python3
from concurrent.futures import ThreadPoolExecutor
from zipfile import *
from operator import itemgetter
from threading import Lock, Thread
import json, sys, random, string, ntpath, time, os, datetime, queue, shutil
import boto3, argparse, importlib

lambda_clients = {}
global_arns = {} # UNUSED BUT INTERESTING
regions = [
	'us-east-2', 'us-east-1','us-west-1','us-west-2','eu-west-3',
	'ap-northeast-1','ap-northeast-2','ap-south-1',
	'ap-southeast-1','ap-southeast-2','ca-central-1',
	'eu-central-1','eu-west-1','eu-west-2','sa-east-1',
]

lock = Lock()
lock_filewrite = Lock()
q = queue.Queue()

outfile = None
debug_global = None
verbose = None

start_time = None
end_time = None
time_lapse = None

role_name = None

def main(args):

	global outfile, debug_global, verbose, start_time, end_time, time_lapse, role_name

	sleep = args.sleep
	target_file = args.file
	num_hosts = args.num_hosts
	ports_list = args.ports
	outfile = args.outfile
	debug_global = args.debug
	verbose = args.verbose

	access_key = args.access_key
	secret_access_key = args.secret_access_key

	# input exception handling
	if outfile != None:
		outfile = outfile + "-dps.txt"
		if os.path.exists(outfile):
			log_entry("File {} already exists, try again with a unique file name".format(outfile))
			return

	if args.config != None:
		log_entry("Loading AWS configuration details from file: {}".format(args.config))
		aws_dict = json.loads(open(args.config).read())
		access_key = aws_dict['access_key']
		secret_access_key = aws_dict['secret_access_key']

	if access_key == None or secret_access_key == None:
		log_entry("Requires AWS access keys, add access keys/secrets or fill out config file")
		return

	if sleep != None and sleep < 0:
		sleep = 0

	if num_hosts < 0:
		log_entry("Input a valid number of scan hosts")
		return

	if num_hosts > 50:
		log_entry("Warning: Large host creation number, may not play nice with AWS, continuing...")

	try:
		ports_list = ','.join(ports_list)
		ports = port_parse(ports_list)
	except:
		log_entry("Invalid port specification")
		return

	load_targets(target_file)

	# Prepare the deployment package
	zip_path = create_zip()

	# Create lambdas based on thread count
	arns = load_lambdas(access_key, secret_access_key, num_hosts, zip_path)

	lambda_args = {
		'ports' : ports,
		'sleep' : sleep
	}
	# Start Spray
	with ThreadPoolExecutor(max_workers=len(arns)) as executor:
		for arn in arns:
			log_entry('Launching scan using {}...'.format(arn[0]))
			executor.submit(
				start_scan,
				access_key=access_key,
				secret_access_key=secret_access_key,
				arn=arn,
				lambda_args=lambda_args
			)


	# Remove AWS resources and build zips
	clean_up(access_key, secret_access_key, len(arns), only_lambdas=False)

	# TODO CHECK - scan cancel handling, clean up infra like CM

# GOOD
def display_stats(start=True):
	if start:
		lambda_count = 0
		for lc, val in lambda_clients.items():
			if val:
				lambda_count += 1

		log_entry('Total Lambdas: {}'.format(lambda_count))


	if end_time and not start:
		log_entry('End Time: {}'.format(end_time))
		log_entry('Total Execution: {} seconds'.format(time_lapse))


# GOOD
def port_parse(ports_list):
	ports = []
	for p in ports_list.split(','):
		if p == '':
			continue
		else:
			if '-' in p:
				for i in range(int(p.split('-')[0]),int(p.split('-')[1])+1):
					ports.append(i)
			else:
				ports.append(int(p))
	return list(set(ports))


def get_lambda_ip(access_key, secret_access_key, arn):

	arn_parts = arn[0].split(':')
	region_id = arn[1]
	region, func = arn_parts[3], arn_parts[-1]
	client = init_client('lambda', access_key, secret_access_key, region, region_id.split('_')[1])

	payload = {}
	payload['ports'] = None
	payload['sleep'] = None
	payload['target'] = None
	payload['region'] = region
	payload['getIP'] = True

	response = client.invoke(
		FunctionName   = func,
		InvocationType = "RequestResponse",
		Payload        = bytearray(json.dumps(payload), 'utf-8')
	)

	return_payload = json.loads(response['Payload'].read().decode("utf-8"))

	return return_payload['sourceIP']


# GOOD
def start_scan(access_key, secret_access_key, arn, lambda_args):

	ip = get_lambda_ip(access_key, secret_access_key, arn)
	log_entry("Lambda Information - region: {reg}, IP: {ip}".format(reg=arn[1].split('_')[0], ip=ip))

	while True:
		target = q.get_nowait()

		if target is None:
			break

		payload = {}
		payload['ports'] = lambda_args['ports']
		payload['sleep'] = lambda_args['sleep']
		payload['getIP'] = False
		payload['sourceIP'] = ip
		payload['target'] = target

		invoke_lambda(
			access_key=access_key,
			secret_access_key=secret_access_key,
			arn=arn,
			payload=payload,
		)

		q.task_done()


# UNUSED
def clear_credentials(username, password):
	global credentials
	c = {}
	c['accounts'] = []
	for x in credentials['accounts']:
		if not x['username'] == username:
			x['success'] = True
			c['accounts'].append(x)
	credentials = c


# GOOD
def load_targets(target_file):
	log_entry('Loading targets from {}'.format(target_file))

	targets = load_file(target_file)

	for target in targets:
		q.put(target)


# GOOD
def load_file(filename):
	if filename:
		return [line.strip() for line in open(filename, 'r')]


# UNUSED
def load_zips(thread_count):
	if thread_count > len(regions):
		thread_count = len(regions)

	use_regions = []
	for r in range(0, thread_count):
		use_regions.append(regions[r])

	with ThreadPoolExecutor(max_workers=thread_count) as executor:
		for region in use_regions:
			zip_list.add(
				executor.submit(
					create_zip,
					plugin=plugin,
					region=region,
				)
			)


# GOOD
def load_lambdas(access_key, secret_access_key, num_hosts, zip_path):

	threads = num_hosts

	if threads > q.qsize():
		threads = q.qsize()

	arns = []
	with ThreadPoolExecutor(max_workers=threads) as executor:
		for x in range(0,threads):
			arns.append(
				executor.submit(
					create_lambda,
					zip_path=zip_path,
					access_key=access_key,
					secret_access_key=secret_access_key,
				)
			)

	return [x.result() for x in arns]


# GOOD
def generate_random(num):
	seed = random.getrandbits(num)
	while True:
	   yield seed
	   seed += 1


# GOOD
def create_zip():
	lambda_path = 'lambda/'
	random_name = next(generate_random(32))
	build_zip = 'build/lambda_{}.zip'.format(random_name)

	with lock:
		log_entry('Creating build deployment DPS lambda')
		shutil.make_archive(build_zip[0:-4], 'zip', lambda_path)

	return build_zip


# UNUSED
def sorted_arns():
	return sorted(
		global_arns.items(),
		key=itemgetter(1),
		reverse=False
	)


# UNUSED
def next_arn():
	if len(global_arns.items()) > 0:
		return sorted_arns()[0][0]


# UNUSED
def update_arns(region_name=None):
	dt = datetime.datetime.now()
	if not region_name:
		for k,v in global_arns.items():
			global_arns[k] = dt
	else:
		global_arns[region_name] = dt


# GOOD
def init_client(service_type, access_key, secret_access_key, region_name, region_id=None):
	ck_client = None

	full_name = region_name+'_'+str(region_id)

	# Reuse Lambda lambda_clients
	if service_type == 'lambda':
		if region_name in lambda_clients.keys():
			if region_id == None:
				return lambda_clients[region_name]
			else:
				return lambda_clients[full_name]

	with lock:
		ck_client = boto3.client(
			service_type,
			aws_access_key_id=access_key,
			aws_secret_access_key=secret_access_key,
			region_name=region_name,
		)

	if service_type == 'lambda':
		if region_id == None:
			lambda_clients[region_name] = ck_client
		else:
			lambda_clients[full_name] = ck_client

	return ck_client


# GOOD
def create_role(access_key, secret_access_key, region_name):
	client = init_client('iam', access_key, secret_access_key, region_name)
	lambda_policy = {
		"Version": "2012-10-17",
	  "Statement": [
		{
		  "Effect": "Allow",
		  "Principal": {
			"Service": "lambda.amazonaws.com"
		  },
		  "Action": "sts:AssumeRole"
		},
		{
		  "Effect": "Allow",
		  "Principal": {
			"Service": "sns.amazonaws.com"
		  },
		  "Action": "sts:AssumeRole"
		}
	  ]
	}

	current_roles = client.list_roles()
	check_roles = current_roles['Roles']
	for current_role in check_roles:
		arn = current_role['Arn']
		role_name = current_role['RoleName']

		if 'DPS_Role' == role_name:
			return arn

	try:
		role_response = client.create_role(RoleName='DPS_Role',
			AssumeRolePolicyDocument=json.dumps(lambda_policy)
		)
		role = role_response['Role']

		return role['Arn']

	except:
		return None


# GOOD
def loop_create_role(access_key, secret_access_key, region_name):

	role_name = None
	while role_name == None:
		role_name = create_role(access_key, secret_access_key, region_name)

	return role_name


# GOOD
def create_lambda(access_key, secret_access_key, zip_path):
	region = random.choice(regions)
	head,tail = ntpath.split(zip_path)
	build_file = tail.split('.')[0]
	lambda_name = build_file.split('_')[0]

	handler_name = '{}.lambda_handler'.format(lambda_name)
	zip_data = None

	with open(zip_path,'rb') as fh:
		zip_data = fh.read()

	try:

		role_name = loop_create_role(access_key, secret_access_key, region)
		log_entry("{reg}: Roles created, sleeping for 15 seconds to allow AWS propagation".format(reg=region), debug=True)
		time.sleep(15)
		region_id = next(generate_random(32))
		build_file = "lambda_" + str(region_id)
		client = init_client('lambda', access_key, secret_access_key, region, region_id)
		response = client.create_function(
				Code={
					'ZipFile': zip_data,
				},
				Description='',
				FunctionName=build_file,
				Handler=handler_name,
				MemorySize=128,
				Publish=True,
				Role=role_name,
				Runtime='python3.6',
				Timeout=8,
				VpcConfig={
				},
			)

		log_entry('Created lambda {} in {}'.format(response['FunctionArn'], region))

		return [response['FunctionArn'], region+'_'+str(region_id)]

	except Exception as ex:
		log_entry('Error creating lambda using {} in {}: {}'.format(zip_path, region, ex))
		return None


# GOOD
def invoke_lambda(access_key, secret_access_key, arn, payload):
	arn_parts = arn[0].split(':')
	region_id = arn[1]
	region, func = arn_parts[3], arn_parts[-1]
	client = init_client('lambda', access_key, secret_access_key, region, region_id.split('_')[1])

	payload['region'] = region

	if payload['sleep'] != 0:
		log_entry("{reg}: Sleeping for {sleep} seconds of delay".format(reg=region, sleep=payload['sleep']), debug=True)
		time.sleep(payload['sleep'])

	response = client.invoke(
		FunctionName   = func,
		InvocationType = "RequestResponse",
		Payload        = bytearray(json.dumps(payload), 'utf-8')
	)

	return_payload = json.loads(response['Payload'].read().decode("utf-8"))

	# {'errorMessage': "Unable to import module 'DPSlambda'"}

	if return_payload['errorMessage'] is not None:
		log_entry(return_payload['errorMessage'])
	else:
		prepend = ''
		if verbose:
			prepend = '{reg}/{ip} - '.format(reg=region, ip=payload['sourceIP'])
		for port_status in return_payload['results']:
			log_entry("{prepend}Discovered {status} port {port}/tcp on {target}".format(prepend=prepend, status=port_status['status'], port=port_status['port'], target=return_payload['target']))


# GOOD
def log_entry(entry, debug=False):
	# TODO add debug flag

	if debug == True and debug_global != True:
		return

	debug_str = ""
	if debug:
		debug_str = "DEBUG: "

	global lock_filewrite

	lock_filewrite.acquire()

	ts = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
	print('[{ts}] {dbg}{entry}'.format(ts=ts, dbg=debug_str, entry=entry))

	if outfile is not None:
		with open(outfile, 'a+') as file:
			file.write('[{}] {}'.format(ts, entry))
			file.write('\n')
			file.close()

	lock_filewrite.release()


# GOOD
def clean_up(access_key, secret_access_key, thread_count, only_lambdas=True):
	if not only_lambdas:
		for region in regions:
			client = init_client('iam', access_key, secret_access_key, region)
			try:
				client.delete_role(RoleName='DPS_Role')
				log_entry('Successfully cleaned DPS_Role in region {}'.format(region))
			except Exception as ex:
				log_entry('Failed to clean DPS_Role in region {reg}: {error}'.format(reg=region,error=ex), debug=True)

	for client_name, client in lambda_clients.items():
		log_entry('Cleaning up lambdas in {}...'.format(client.meta.region_name))

		try:
			lambdas_functions = client.list_functions(
				FunctionVersion='ALL',
				MaxItems=1000
			)

			if lambdas_functions:
				for lambda_function in lambdas_functions['Functions']:
					if not '$LATEST' in lambda_function['FunctionArn']:
						lambda_name = lambda_function['FunctionName']
						arn = lambda_function['FunctionArn']
						try:
							log_entry('Destroying {} in region: {}'.format(arn, client.meta.region_name))
							client.delete_function(FunctionName=lambda_name)
						except:
							log_entry('Failed to clean-up {} using client region {}'.format(arn, region))
		except:
			log_entry('Failed to connect to client region {}'.format(region))

	filelist = [ f for f in os.listdir('build') if f.endswith(".zip") ]
	for f in filelist:
		os.remove(os.path.join('build', f))


if __name__ == '__main__':

	parser = argparse.ArgumentParser()
	parser.add_argument('-f', '--file', help='Input Target File', required=True)
	parser.add_argument('-n', '--num-hosts', help='Number of spray hosts to be created', type=int, required=True)
	parser.add_argument('-p', '--ports', help='Ports list', required=True, nargs='+') # TODO CHECK - type of a list? want csv list of ports as input
	parser.add_argument('-s', '--sleep', help='Sleep time for a host to wait between target scans', type=int, default=0, required=False)
	parser.add_argument('-o', '--outfile', help='Output file to write contents', required=False)
	parser.add_argument('-d', '--debug', help='Output debug content', action="store_true", default=False, required=False)
	parser.add_argument('-v', '--verbose', help='Output region & source IP addresses for scan host output', action="store_true", default=False, required=False)
	parser.add_argument('--access_key', help='aws access key', required=False)
	parser.add_argument('--secret_access_key', help='aws secret access key', required=False)
	parser.add_argument('--config', help='Authenticate to AWS using config file aws.config', type=str, default=None)


	parser.add_argument('--cleanup-roles', help='NOT FUNCTIONAL Cleanup DPS roles from regions', type=str, default=None)
	parser.add_argument('--cleanup-lambdas', help='NOT FUNCTIONAL Cleanup all DPS created lambdas', type=str, default=None)
	parser.add_argument('--cleanup-all', help='NOT FUNCTIONAL Cleanup all DPS related AWS infra', type=str, default=None)

	args = parser.parse_args()
	main(args)
