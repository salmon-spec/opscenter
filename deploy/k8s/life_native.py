"""Independent browser origins keep upstream scripts away from workbench storage."""
PORTS = {'subscriptions': (31191, 31591), 'home-inventory': (31192, 31592), 'life-reminders': (31193, 31593)}
PARENTS = ['http://10.66.66.13:30088', 'https://10.66.66.13']


def gateway(upstream_port):
    parents = ' '.join(PARENTS)
    return '''{
    auto_https off
    admin off
}
(native) {
    header {
        Content-Security-Policy "frame-ancestors PARENTS"
        Referrer-Policy "no-referrer"
        X-Content-Type-Options "nosniff"
        -X-Frame-Options
    }
    @launch path /_ops/launch
    handle @launch {
        reverse_proxy 127.0.0.1:8080
    }
    handle {
        forward_auth 127.0.0.1:8080 {
            uri /_ops/verify
            copy_headers X-Native-Cookie
            header_up -Upgrade
            header_up -Connection
        }
        reverse_proxy 127.0.0.1:UPSTREAM {
            header_up Cookie {http.request.header.X-Native-Cookie}
            header_up -X-Native-Cookie
            header_down -X-Frame-Options
            header_down Content-Security-Policy "frame-ancestors[^;]*" "frame-ancestors PARENTS"
            header_down Set-Cookie "^(PHPSESSID|wallos_login|hb[.]auth[.]token|vikunja_refresh_token)=" "{args[0]}$1="
            flush_interval -1
        }
    }
}
http://:8082 {
    import native http_
}
https://:8443 {
    tls /run/native-tls/tls.crt /run/native-tls/tls.key
    import native tls_
}
'''.replace('PARENTS', parents).replace('UPSTREAM', str(upstream_port))


def resources(key, tag, upstream_port):
    name = 'module-' + key
    http, https = PORTS[key]
    return [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name + '-native', 'namespace': 'opscenter-life'},
         'data': {'Caddyfile': gateway(upstream_port)}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': name + '-native', 'namespace': 'opscenter-life'},
         'spec': {'type': 'NodePort', 'selector': {'app': name}, 'ports': [
             {'name': 'http', 'port': http, 'nodePort': http, 'targetPort': 8082},
             {'name': 'https', 'port': https, 'nodePort': https, 'targetPort': 8443}]}}
    ]
