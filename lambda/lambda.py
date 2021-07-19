import botocore.vendored.requests as requests #As long as we're in AWS Lambda, this trick works for accessing requests
import json, datetime, socket, time
import urllib.request



def lambda_handler(event, context):
	return scan(event['target'], event['ports'], event['getIP'])


def scan(target, ports, getIP):

	data_response = {
		'results' : [],
		'target' : target,
		'ports' : ports,
		'sourceIP' : '',
		'errorMessage' : None
	}

	if getIP:
		# with urllib.request.urlopen('http://icanhazip.com') as f:
		# 	data_response['sourceIP'] = str(f.read(300).strip())
		data_response['sourceIP'] = requests.get('http://icanhazip.com').text.strip()
		return data_response

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


print(scan("scanme.nmap.org",['443'],True))
