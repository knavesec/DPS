import json, datetime, socket, time

def lambda_handler(event, context):
	return scan(event['target'], event['ports'])


def scan(target, ports):

	data_response = {
		'results' : [],
		'target' : target,
		'ports' : ports,
		'errorMessage' : None
	}

	try:
		for port in ports:

			s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			socket.setdefaulttimeout(3)

			result = s.connect_ex((target, port))

			port_status = {
				'port' : port,
				'status' : None
			}
			if result == 0:
				port_status['status'] = "open"
			else:
				port_status['status'] = "closed"
			data_response['results'].append(port_status)

			s.close()


	except Exception as ex:
		data_response['errorMessage'] = ex
		pass

	return data_response
