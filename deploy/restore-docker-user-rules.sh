#!/bin/bash
# Restore DOCKER-USER rules after Docker restart
# Updated: added ports 9091 (OpsCenter) and 8890/8891 (Harbor)
iptables -F DOCKER-USER
# Allow established connections
iptables -I DOCKER-USER -j RETURN -m conntrack --ctstate ESTABLISHED,RELATED
# Allow DNS outbound (for container DNS resolution)
iptables -I DOCKER-USER -p udp --dport 53 -j RETURN
iptables -I DOCKER-USER -p tcp --dport 53 -j RETURN
# Allow HTTPS outbound (for container API/RSS fetching)
iptables -I DOCKER-USER -p tcp --dport 443 -j RETURN
# Allow HTTP outbound
iptables -I DOCKER-USER -p tcp --dport 80 -j RETURN
# Allow inbound ports (from external to containers via Docker proxy)
iptables -I DOCKER-USER -p tcp --dport 80 -j RETURN
iptables -I DOCKER-USER -p tcp --dport 443 -j RETURN
iptables -I DOCKER-USER -p tcp --dport 8443 -j RETURN
iptables -I DOCKER-USER -p tcp --dport 2222 -j RETURN
# Harbor Web UI
iptables -I DOCKER-USER -p tcp --dport 8890 -j RETURN
iptables -I DOCKER-USER -p tcp --dport 8891 -j RETURN
# OpsCenter backend (container-to-host access)
iptables -I DOCKER-USER -p tcp --dport 9091 -j RETURN
# Drop everything else
iptables -A DOCKER-USER -j DROP
