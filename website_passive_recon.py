# -*- coding: utf-8 -*-
"""
Script to automate, when possible, the passive reconnaissance performed a website prior to an assessment.
Also used to guide the reconnaissance phase by defining all steps (manual or automated).

See README.md file for API Key INI file example.
"""
import colorama
import argparse
import configparser
import dns.resolver
import requests
import time
import sys
import socket
import datetime
import tldextract
import json
from termcolor import colored
from dns.resolver import NoAnswer
from dns.resolver import NoNameservers
from dns.resolver import NXDOMAIN
from requests.exceptions import ProxyError


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:70.0) Gecko/20100101 Firefox/70.0"


def get_intelx_infos(ip_or_domain, api_key, http_proxy):
    web_proxies = configure_proxy(http_proxy)
    infos = []   
    # Intelligence X free API have hits credtis depending on the service consumed
    # A new account must be created after consumed all credits 
    # See https://intelx.io/account?tab=developer
    request_headers = {"User-Agent": USER_AGENT, "x-key": api_key}
    payload = {
        "term": ip_or_domain,
        "buckets": [],
        "lookuplevel": 0,
        "maxresults": 100,
        "timeout": 0,
        "datefrom": "",
        "dateto": "",
        "sort": 4,
        "media": 0,
        "terminate": []
    }     
    # First we must do a search
    service_url = f"https://2.intelx.io/intelligent/search"    
    response = requests.post(service_url, data=json.dumps(payload), headers=request_headers, proxies=web_proxies, verify=(http_proxy is None))
    if response.status_code != 200:
        infos.append(f"HTTP response code {response.status_code} received for the search!")
        return infos
    # Then get the result for the search
    search_id = str(response.json()["id"])
    service_url += f"/result?id={search_id}"
    response = requests.get(service_url, headers=request_headers, proxies=web_proxies, verify=(http_proxy is None))
    # Count result by bucket
    if response.status_code != 200:
        infos.append(f"HTTP response code {response.status_code} received for the result of the search!")
        return infos
    data = response.json()
    buckets = {}
    pasties = {}
    if "records" in data:
        for record in data["records"]:
            if "bucket" in record:
                bucket_name = record["bucket"]
                # Special processing for Pasties, we extract URL and added date...
                if bucket_name.lower() == "pastes" and "keyvalues" in record:
                    for paste in record["keyvalues"]:
                        value = paste["value"] # Contains the paste URL
                        pasties[value] = record["added"]
                if bucket_name not in buckets:
                    buckets[bucket_name.lower()] = 0
                buckets[bucket_name.lower()] += 1
    # Add the information
    for bucket_name in buckets:
        infos.append(f"{buckets[bucket_name]} records for bucket {bucket_name}")
    for paste in pasties:
        infos.append(f"Paste '{paste}' added on {pasties[paste]}")
    return infos


def extract_infos_from_virus_total_response(http_response):
    infos = []
    if http_response.status_code != 200:
        if http_response.status_code != 204:
            infos.append(f"HTTP response code {http_response.status_code} received!")
        else:
            infos.append(f"Request rate limit exceeded: Wait one minute and re-run the script!")
    else:
        results = http_response.json() 
        # From VT API doc regarding the "response_code" property: 
        # If the item you searched for was not present in VirusTotal's dataset this result will be 0. 
        # If the requested item is still queued for analysis it will be -2. 
        # If the item was indeed present and it could be retrieved it will be 1.
        # See https://developers.virustotal.com/reference#api-responses
        rc = results["response_code"]
        msg = results["verbose_msg"]        
        infos.append(f"Presence = {msg}")        
        if rc == 1:
            urls_detected_count = 0
            urls_undetected_count = 0
            samples_detected_download_count = 0
            samples_undetected_download_count = 0
            if "detected_urls" in results:
                urls_detected_count = len(results["detected_urls"])
            if "undetected_urls" in results:
                urls_undetected_count = len(results["undetected_urls"])
            if "detected_downloaded_samples" in results:
                samples_detected_download_count = len(results["detected_downloaded_samples"])
            if "undetected_downloaded_samples" in results:
                samples_undetected_download_count = len(results["undetected_downloaded_samples"])
            infos.append(f"URLs at this IP address that have at least one detection on a URL scan = {urls_detected_count}")   
            infos.append(f"URLs at this IP address with no detections on a URL scan = {urls_undetected_count}")                   
            infos.append(f"Files that have been downloaded from this IP address with at least one AV detection = {samples_detected_download_count}")                   
            infos.append(f"Files that have been downloaded from this IP address with zero AV detections = {samples_undetected_download_count}")
        elif rc == -2:
            infos.append(f"Pending analysis for this item.")                             
    return infos


def get_domain_without_tld(domain):
    domain_infos = tldextract.extract(domain)
    domain_no_tld = f"{domain_infos.domain}"
    if len(domain_infos.subdomain) > 0:
        domain_no_tld = f"{domain_infos.subdomain}.{domain_infos.domain}"
    return domain_no_tld


def get_parent_domain(domain):
    parent_domain = None
    domain_infos = tldextract.extract(domain)
    if len(domain_infos.subdomain) > 0:
        parent_domain = f"{domain_infos.domain}.{domain_infos.suffix}"
    return parent_domain


def configure_proxy(http_proxy):
    web_proxies = {}
    if http_proxy is not None:
        web_proxies["http"] = http_proxy
        web_proxies["https"] = http_proxy
    return web_proxies    


def test_proxy_connectivity(http_proxy):
    msg = ""
    try:
        web_proxies = {"http": http_proxy, "https": http_proxy}
        service_url = "https://perdu.com/"
        response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None), timeout=20)
        if response.status_code == 200:
            msg = "Succeed"
        else:
            msg = f"Failed (HTTP response code = {response.status_code})"
    except ProxyError as e:
        msg = f"Failed ({str(e)})"
    return msg


def print_infos(info_list, prefix=""):
    if len(info_list) == 0:
        print(f"{prefix}No data found")
    else:        
        for info in info_list:
            print(f"{prefix}{info}")


def do_whois_request(ip, whois_server):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((whois_server, 43))
    s.send((ip + "\r\n").encode())
    response = b""
    while True:
        data = s.recv(4096)
        response += data
        if not data:
            break
    s.close()
    return response.decode()


def do_whois(ip):
    whois_org = ["arin", "lacnic", "afrinic", "ripe", "apnic"]
    whois_server_tpl = "whois.%s.net"
    # First try with ARIN
    whois_response = do_whois_request(ip, whois_server_tpl % "arin")
    for line in whois_response.splitlines():
        if line.strip().startswith("Ref:"):
            # IP block is not managed by ARIN so we call the provided org in the Ref link
            link = line[4:].strip(" ")
            org = link.split("/")[-1]
            if org.lower() in whois_org:
                whois_response = do_whois_request(ip, whois_server_tpl % org)
                break
    return whois_response
            

def get_ip_addresses(domain, name_server, record_types):
    ips = []
    resolver = dns.resolver.Resolver(configure=True)
    if name_server is not None:
        resolver.nameservers = [name_server]
    for record_type in record_types:
        try:
            answer = resolver.query(domain, record_type)
            for data in answer:
                ips.append(data.to_text())
        except NoAnswer:
            pass
        except NoNameservers:
            pass
        except NXDOMAIN:
            pass
    return ips


def get_cnames(domain, name_server):
    cnames = []
    resolver = dns.resolver.Resolver(configure=True)
    if name_server is not None:
        resolver.nameservers = [name_server]
    try:
        answer = resolver.query(domain, "CNAME")
        for data in answer:
            cnames.append(data.target.to_text())
    except NoAnswer:
        pass
    return cnames


def get_active_shared_hosts(ip, http_proxy):
    web_proxies = configure_proxy(http_proxy)
    infos = []
    # HackerTarget API is limited of 50 queries per day
    # See https://hackertarget.com/ip-tools/
    service_url = f"https://api.hackertarget.com/reverseiplookup/?q={ip}"
    response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None))
    if response.status_code != 200:
        infos.append(f"HTTP response code {response.status_code} received!")
        return infos
    vhosts = response.text
    for vhost in vhosts.splitlines():
        if vhost != ip:
            infos.append(vhost)
    return infos


def get_passive_shared_hosts(ip, http_proxy):
    web_proxies = configure_proxy(http_proxy)
    infos = []
    # See https://www.threatminer.org/api.php
    service_url = f"https://api.threatminer.org/v2/host.php?q={ip}&rt=2"
    response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None))
    if response.status_code != 200:
        infos.append(f"HTTP response code {response.status_code} received from ThreatMiner API !")
    else: 
        results = response.json()
        if results["status_code"] == "200":
            for result in results["results"]:
                vhost = result["domain"].split(":")[0]
                if vhost not in infos:
                    infos.append(vhost)
    # See https://github.com/AlienVault-OTX/ApiV2
    service_url = f"https://www.threatcrowd.org/searchApi/v2/ip/report/?ip={ip}"
    response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None))
    if response.status_code != 200:
        infos.append(f"HTTP response code {response.status_code} received from ThreatCrowd API !")
    else:
        results = response.json()
        if "resolutions" in results:
            for result in results["resolutions"]:
                vhost = result["domain"]
                if vhost not in infos:
                    infos.append(vhost)
    return infos    


def get_ip_owner(ip, http_proxy):
    infos = []
    data = do_whois(ip)
    records = data.splitlines()
    records_skip_prefix = ["Ref:", "OrgTech", "OrgAbuse", "OrgNOC", "tech-c", "admin-c", "remarks", "e-mail", "abuse", "Comment", "#", "%"]
    for record in records:
        if len(record.strip()) == 0:
            continue
        skip_it = False
        for prefix in records_skip_prefix:
            if record.startswith(prefix):
                skip_it = True
                break
        if not skip_it:
            infos.append(record)    
    return infos


def get_shodan_ip_infos(ip, api_key, http_proxy):
    web_proxies = configure_proxy(http_proxy)         
    infos = []
    # See https://developer.shodan.io/api
    service_url = f"https://api.shodan.io/shodan/host/{ip}?key={api_key}&minify=true"
    response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None))
    if response.status_code != 200:
        infos.append(f"HTTP response code {response.status_code} received!")
        return infos    
    data = response.json() 
    value = data["last_update"]
    infos.append(f"Last update = {value}")
    value = data["isp"]
    infos.append(f"ISP = {value}")
    value = data["org"]
    infos.append(f"Organization = {value}")
    value = " , ".join(data["hostnames"])
    infos.append(f"Hostnames = {value}")
    value = data["ports"]
    infos.append(f"Ports = {value}")
    return infos


def get_qualys_sslscan_cached_infos(domain, ip, http_proxy):
    web_proxies = configure_proxy(http_proxy)               
    infos = []
    # See https://github.com/ssllabs/ssllabs-scan/blob/master/ssllabs-api-docs-v3.md
    service_url = f"https://api.ssllabs.com/api/v3/getEndpointData?host={domain}&s={ip}&fromCache=on"
    response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None))
    if "errors" not in response.text and "statusMessage" not in response.text:
        infos.append(f"HTTP response code {response.status_code} received!")
        return infos    
    data = response.json()
    if "errors" in data:
        error_msg = ""
        for error in data["errors"]:
            error_msg += error["message"]
        infos.append(f"{error_msg}")   
    if "statusMessage" in data and data["statusMessage"] == "Ready":
        if "ipAddress" in data:
            value = data["ipAddress"]
            infos.append(f"IPAddress = {value}")   
        if "serverName" in data:
            value = data["serverName"]
            infos.append(f"ServerName = {value}")   
        if "grade" in data:
            value = data["grade"]
            infos.append(f"Grade = {value}")   
        if "details" in data:
            details = data["details"]
            value = datetime.datetime.fromtimestamp(details["hostStartTime"]/1000.0).strftime("%Y-%d-%mT%H:%M:%S")
            infos.append(f"AssessmentStartingTime = {value}")                           
            value = details["vulnBeast"]
            infos.append(f"BEAST = {value}")      
            value = details["heartbleed"]
            infos.append(f"HEARTBLEED = {value}")             
            value = details["poodle"]       
            infos.append(f"POODLE = {value}")           
            value = details["freak"]        
            infos.append(f"FREAK = {value}")                 
            value = details["logjam"]        
            infos.append(f"LOGJAM = {value}")  
            value = details["drownVulnerable"]        
            infos.append(f"DROWN = {value}")          
            value = (details["ticketbleed"] == 2)
            infos.append(f"TICKETBLEED = {value}")           
            value = (details["bleichenbacher"] == 2 or details["bleichenbacher"] == 3)
            infos.append(f"ROBOT = {value}")           
    return infos  


def get_hybrid_analysis_report_infos(query, api_key, http_proxy):
    web_proxies = configure_proxy(http_proxy)              
    infos = []
    # See https://www.hybrid-analysis.com/docs/api/v2
    service_url = f"https://www.hybrid-analysis.com/api/search?query={query}"
    response = requests.get(service_url, headers={"User-Agent": "Falcon", "api-key": api_key}, proxies=web_proxies, verify=(http_proxy is None))
    if response.status_code != 200:
        infos.append(f"HTTP response code {response.status_code} received!")
        return infos    
    data = response.json()
    rc = data["response_code"] 
    if rc == 0 :
        if len(data["response"]["result"]) > 0:
            result = data["response"]["result"][0]
            infos.append(f"Verdict = {result['verdict'].capitalize()}")
            infos.append(f"Analysis time = {result['start_time']}")
        else:
            infos.append("No report found")
    else:
        infos.append(f"Call to API failed (RC = {rc})")
    return infos


def get_virus_total_report_infos(domain, parent_domain, ip_list, api_key, http_proxy):
    web_proxies = configure_proxy(http_proxy)              
    infos = {}
    # See https://developers.virustotal.com/reference
    # Note: As VT as a limit of 4 requests by minute then this function
    # handle globally the retrieval of infos from VT and handle this limitation
    # Get info for the domains: domain + parent domain
    vt_call_count = 0
    for domain in [domain, parent_domain]:
        if domain is not None:
            service_url = f"https://www.virustotal.com/vtapi/v2/domain/report?apikey={api_key}&domain={domain}"
            response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None))            
            vt_call_count += 1
            infos[domain] = extract_infos_from_virus_total_response(response)
    # Get info for the IPs
    for ip in ip_list:
        if vt_call_count > 4:
            time.sleep(60)
            vt_call_count = 0
        service_url = f"https://www.virustotal.com/vtapi/v2/ip-address/report?apikey={api_key}&ip={ip}"
        response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None))            
        vt_call_count += 1
        infos[ip] = extract_infos_from_virus_total_response(response)
    return infos


def get_certificate_transparency_log_subdomains(domain, http_proxy):
    web_proxies = configure_proxy(http_proxy)
    infos = []
    # See https://crt.sh
    service_url = f"https://crt.sh/?q=%.{domain}&output=json"
    response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None))
    if response.status_code != 200:
        infos.append(f"HTTP response code {response.status_code} received!")
        return infos    
    results = response.json() 
    for entry in results:
        cert_name = f"{entry['name_value']} ({entry['issuer_name']})"
        if cert_name not in infos:
            infos.append(cert_name)
    return infos


def get_github_repositories(domain_or_ip, http_proxy):
    web_proxies = configure_proxy(http_proxy)
    infos = []
    # See # See https://developer.github.com/v3/search/#search-repositories
    term = f"%22{domain_or_ip}%22"
    service_url = f"https://api.github.com/search/repositories?q=size:%3E0+{term}&sort=updated&order=desc"
    response = requests.get(service_url, headers={"User-Agent": USER_AGENT}, proxies=web_proxies, verify=(http_proxy is None))    
    if response.status_code != 200:
        infos.append(f"HTTP response code {response.status_code} received!")
        return infos    
    results = response.json()
    for repo in results["items"]:
        html_url = repo["html_url"]
        is_fork = repo["fork"]
        forks = repo["forks"]
        watchers = repo["watchers"]
        infos.append(f"{html_url} (IsFork: {is_fork} - Forks: {forks} - Watchers: {watchers})")
    return infos    


if __name__ == "__main__":
    requests.packages.urllib3.disable_warnings()
    colorama.init()
    start_time = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", action="store", dest="domain_name", help="Domain to analyse (ex: www.righettod.eu).", required=True)
    parser.add_argument("-a", action="store", dest="api_key_file", default=None, help="Configuration INI file with all API keys (ex: conf.ini).", required=False)
    parser.add_argument("-n", action="store", dest="name_server", default=None, help="Name server to use for the DNS query (ex: 8.8.8.8).", required=False)
    parser.add_argument("-p", action="store", dest="http_proxy", default=None, help="HTTP proxy to use for all HTTP call to differents services (ex: http://88.198.50.103:9080).", required=False)
    args = parser.parse_args()
    api_key_config = configparser.ConfigParser()
    api_key_config["API_KEYS"] = {}
    http_proxy_to_use = args.http_proxy
    print(colored(f"##############################################", "white", attrs=["bold"]))
    print(colored(f"### TARGET: {args.domain_name.upper()}", "white", attrs=["bold"]))
    print(colored(f"##############################################", "white", attrs=["bold"]))
    if args.api_key_file is not None:
        api_key_config.read(args.api_key_file)        
        api_keys_names = " / ".join(api_key_config["API_KEYS"].keys())
        print(colored(f"[CONF] API key file '{args.api_key_file}' loaded: {api_keys_names}.", "white", attrs=[]))
    if args.name_server is not None:
        print(colored(f"[CONF] Name server '{args.name_server}' used for all DNS queries.", "white", attrs=[]))
    else:
        print(colored(f"[CONF] System default name server used for all DNS queries.", "white", attrs=[]))
    if http_proxy_to_use is not None:
        print(colored(f"[CONF] HTTP proxy '{http_proxy_to_use}' used for all HTTP requests.", "white", attrs=[]))
        if args.api_key_file is not None:
            print(colored(f"[WARNING] Be aware that your API keys will be visible by the specified proxy!", "yellow", attrs=["bold"]))
        print("Test proxy connectivity...", end='')
        msg = test_proxy_connectivity(http_proxy_to_use)
        if msg.startswith("Succeed"):
            print(colored(msg, "green", attrs=[]))
        else:
            print(colored(msg, "red", attrs=[]))
            print(colored(f".::Reconnaissance aborted::.", "red", attrs=["bold"]))
            sys.exit(1)            
    else:
        print(colored(f"[CONF] No HTTP proxy used for all HTTP requests.", "white", attrs=["bold"]))
    print(colored(f"[DNS] Extract the IP V4/V6 addresses...","blue", attrs=["bold"]))
    ips = get_ip_addresses(args.domain_name, args.name_server, ["A", "AAAA"])
    if not ips:
        print(colored(f".::Unable to resolve DNS - Reconnaissance aborted::.", "red", attrs=["bold"]))
        sys.exit(2)
    print_infos(ips)
    print(colored(f"[DNS] Extract the aliases...", "blue", attrs=["bold"]))
    cnames = get_cnames(args.domain_name, args.name_server)
    print_infos(cnames)
    print(colored(f"[WHOIS] Extract the owner information of the IP addresses...", "blue", attrs=["bold"]))
    for ip in ips:
        print(colored(f"{ip}", "yellow", attrs=["bold"]))
        informations = get_ip_owner(ip, http_proxy_to_use)
        print_infos(informations, "  ")
    print(colored(f"[SHODAN] Extract the information of the IP addresses, domain and parent domain...", "blue", attrs=["bold"]))
    if "shodan" in api_key_config["API_KEYS"]:
        api_key = api_key_config["API_KEYS"]["shodan"]
        print(colored(f"{args.domain_name}", "yellow", attrs=["bold"]))
        print("  Search with filter using the API with a free tier API key is not allowed, so, use the following URL from a browser:")
        print(f"    https://www.shodan.io/search?query=hostname%3A{args.domain_name}")
        parent_domain = get_parent_domain(args.domain_name)
        if parent_domain != None:
            print(f"    https://www.shodan.io/search?query=hostname%3A{parent_domain}")            
        is_single_ip = len(ips) < 2
        for ip in ips:
            print(colored(f"{ip}", "yellow", attrs=["bold"]))
            informations = get_shodan_ip_infos(ip, api_key, http_proxy_to_use)
            print_infos(informations, "  ")
            # Add tempo due to API limitation (API methods are rate-limited to 1 request by second)
            if not is_single_ip:
                time.sleep(1)
    else:
        print(colored(f"Skipped because no API key was specified!","red", attrs=["bold"]))
    print(colored(f"[HACKERTARGET] Extract current hosts shared by each IP address (active DNS)...", "blue", attrs=["bold"]))
    for ip in ips:
        print(colored(f"{ip}", "yellow", attrs=["bold"]))
        if ":" in ip:
            print_infos(["IPV6 not supported"], "  ")
            continue
        informations = get_active_shared_hosts(ip, http_proxy_to_use)
        print_infos(informations, "  ")
    print(colored(f"[THREATMINER+THREATCROWD] Extract previous hosts shared by each IP address (passive DNS)...", "blue", attrs=["bold"]))
    for ip in ips:
        print(colored(f"{ip}", "yellow", attrs=["bold"]))
        informations = get_passive_shared_hosts(ip, http_proxy_to_use)
        print_infos(informations, "  ")
    print(colored(f"[NETCRAFT] Provide the URL to report for the IP addresses, domain and parent domain...", "blue", attrs=["bold"]))
    print("No API provided and browser required, so, use the following URL from a browser:")
    print(f"  https://toolbar.netcraft.com/site_report?url={args.domain_name}")
    parent_domain = get_parent_domain(args.domain_name)
    if parent_domain != None:
        print(f"  https://toolbar.netcraft.com/site_report?url={parent_domain}")            
    for ip in ips:
        print(f"  https://toolbar.netcraft.com/site_report?url={ip}")
    print(colored(f"[PASTEBIN via GOOGLE] Provide the URL for dork for the domain and parent domain...", "blue", attrs=["bold"]))
    print("Use the following URL from a browser:")
    print(f"  https://www.google.com/search?q=site%3Apastebin.com+%22{args.domain_name}%22&oq=site%3Apastebin.com+%22{args.domain_name}%22")
    parent_domain = get_parent_domain(args.domain_name)
    if parent_domain != None:
        print(f"  https://www.google.com/search??q=site%3Apastebin.com+%22{parent_domain}%22&oq=site%3Apastebin.com+%22{parent_domain}%22")
    print(colored(f"[GOOGLE] Provide the URL for dork for the domain and parent domain...", "blue", attrs=["bold"]))
    print("Use the following URL from a browser:")
    print(f"  https://www.google.com/search?q=site%3A{args.domain_name}&oq=site%3A{args.domain_name}")
    parent_domain = get_parent_domain(args.domain_name)
    if parent_domain != None:
        print(f"  https://www.google.com/search?q=site%3A{parent_domain}&oq=site%3A{parent_domain}")
    print(colored(f"[WAYBACKMACHINE] Provide the URL for Internet Archive for the domain and parent domain...", "blue", attrs=["bold"]))
    print("Use the following URL from a browser:")
    print(f"  https://web.archive.org/web/*/https://{args.domain_name}")
    print(f"  https://web.archive.org/web/*/http://{args.domain_name}")
    parent_domain = get_parent_domain(args.domain_name)
    if parent_domain != None:
        print(f"  https://web.archive.org/web/*/https://{parent_domain}")
        print(f"  https://web.archive.org/web/*/http://{parent_domain}")
    print(colored(f"[QUALYS] Extract information from SSL cached scan for the domain and IP addresses...", "blue", attrs=["bold"]))
    for ip in ips:
        print(colored(f"{ip}", "yellow", attrs=["bold"]))
        informations = get_qualys_sslscan_cached_infos(args.domain_name, ip, http_proxy_to_use)
        print_infos(informations, "  ")
    print(colored(f"[HYBRID-ANALYSIS] Extract the verdict for the IP addresses, domain and parent domain regarding previous hosting of malicious content...", "blue", attrs=["bold"]))
    if "hybrid-analysis" in api_key_config["API_KEYS"]:
        api_key = api_key_config["API_KEYS"]["hybrid-analysis"]
        print(colored(f"{args.domain_name}", "yellow", attrs=["bold"]))
        informations = get_hybrid_analysis_report_infos(f"domain:{args.domain_name}", api_key, http_proxy_to_use)
        print_infos(informations, "  ")
        parent_domain = get_parent_domain(args.domain_name)
        if parent_domain != None:
            print(colored(f"{parent_domain}", "yellow", attrs=["bold"]))
            informations = get_hybrid_analysis_report_infos(f"domain:{parent_domain}", api_key, http_proxy_to_use)
            print_infos(informations, "  ")
        for ip in ips:
            print(colored(f"{ip}", "yellow", attrs=["bold"]))   
            informations = get_hybrid_analysis_report_infos(f"host:%22{ip}%22", api_key, http_proxy_to_use)
            print_infos(informations, "  ")
    else:
        print(colored(f"Skipped because no API key was specified!","red", attrs=["bold"]))     
    print(colored(f"[VIRUSTOTAL] Extract the presence for the IP addresses, domain and parent domain regarding previous hosting of malicious content...", "blue", attrs=["bold"]))
    if "virustotal" in api_key_config["API_KEYS"]:
        api_key = api_key_config["API_KEYS"]["virustotal"]
        global_informations = get_virus_total_report_infos(args.domain_name, get_parent_domain(args.domain_name), ips, api_key, http_proxy_to_use)
        for k in global_informations:
            print(colored(f"{k}", "yellow", attrs=["bold"]))   
            informations = global_informations[k]
            print_infos(informations, "  ")            
    else:
        print(colored(f"Skipped because no API key was specified!","red", attrs=["bold"]))
    print(colored(f"[CERTIFICATE-TRANSPARENCY] Extract the referenced subdomains of the target domain and parent domain...", "blue", attrs=["bold"]))     
    print(colored(f"{args.domain_name}", "yellow", attrs=["bold"]))
    informations = get_certificate_transparency_log_subdomains(args.domain_name, http_proxy_to_use)
    print_infos(informations, "  ")
    parent_domain = get_parent_domain(args.domain_name)
    if parent_domain != None:
        print(colored(f"{parent_domain}", "yellow", attrs=["bold"]))
        informations = get_certificate_transparency_log_subdomains(parent_domain, http_proxy_to_use)
        print_infos(informations, "  ")
    print(colored(f"[GITHUB] Extract the repositories with references to the IP addresses, domain and parent domain...", "blue", attrs=["bold"]))     
    print(colored(f"{args.domain_name}", "yellow", attrs=["bold"]))
    informations = get_github_repositories(args.domain_name, http_proxy_to_use)
    print_infos(informations, "  ")    
    parent_domain = get_parent_domain(args.domain_name)
    if parent_domain != None:
        print(colored(f"{parent_domain}", "yellow", attrs=["bold"]))
        informations = get_github_repositories(parent_domain, http_proxy_to_use)
        print_infos(informations, "  ")
    print(colored(f"Special search without the TLD for the domain and parent domain", "yellow", attrs=["bold"]))
    domain_no_tld = get_domain_without_tld(args.domain_name)
    print(colored(f"  {domain_no_tld}", "yellow", attrs=["bold"]))
    informations = get_github_repositories(domain_no_tld, http_proxy_to_use)
    print_infos(informations, "    ") 
    if parent_domain != None:
        parent_domain_no_tld = get_domain_without_tld(parent_domain)
        print(colored(f"  {parent_domain_no_tld}", "yellow", attrs=["bold"]))
        informations = get_github_repositories(parent_domain_no_tld, http_proxy_to_use)
        print_infos(informations, "    ")
    for ip in ips:
        print(colored(f"{ip}", "yellow", attrs=["bold"]))   
        informations = get_github_repositories(ip, http_proxy_to_use)
        print_infos(informations, "  ")
    print(colored(f"[INTELX] Check if the site have information about the IP addresses, domain and parent domain...", "blue", attrs=["bold"]))
    print(colored("[i]","green") + " INTELX keep a copy of pastes identified so if a paste was removed then it can be still accessed via the INTELX site.")
    if "intelx" in api_key_config["API_KEYS"]:
        api_key = api_key_config["API_KEYS"]["intelx"]
        infos_for_ip = {}
        for ip in ips:
            infos_for_ip[ip] = get_intelx_infos(ip, api_key, http_proxy_to_use)
        infos_for_domain = get_intelx_infos(args.domain_name, api_key, http_proxy_to_use)
        infos_for_parent_domain = []
        parent_domain = get_parent_domain(args.domain_name)
        if parent_domain != None:
            infos_for_parent_domain = get_intelx_infos(parent_domain, api_key, http_proxy_to_use)
        for ip in ips:
            print(colored(f"{ip}", "yellow", attrs=["bold"]))
            if len(infos_for_ip[ip]) > 0:
                print("  Data found (see below), so, use the following URL from a browser:")
                print(f"    https://intelx.io/?s={ip}")               
            print_infos(infos_for_ip[ip], "  ")
        print(colored(f"{args.domain_name}", "yellow", attrs=["bold"]))
        if len(infos_for_domain) > 0:
            print("  Data found (see below), so, use the following URL from a browser:")
            print(f"    https://intelx.io/?s={args.domain_name}")               
        print_infos(infos_for_domain, "  ")   
        if len(infos_for_parent_domain) > 0:
            print(colored(f"{parent_domain}", "yellow", attrs=["bold"]))
            print("  Data found (see below), so, use the following URL from a browser:")
            print(f"    https://intelx.io/?s={parent_domain}")               
            print_infos(infos_for_parent_domain, "  ")                        
    else:
        print(colored(f"Skipped because no API key was specified!","red", attrs=["bold"]))
    delay = round(time.time() - start_time, 2)      
    print("")     
    print(".::" + colored(f"Reconnaissance finished in {delay} seconds", "green", attrs=["bold"]) + "::.")