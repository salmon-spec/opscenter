/* OpsCenter Terminal Simulation (P3-02: Isolated demo logic) */
/* All output is simulated — not real SSH data */
window.OpsTerminalSim = {

  init(context) {
    return [
      { type: 'info', text: '═══ OpsCenter Terminal v2.0 ═══' },
      { type: 'info', text: 'Phase 1 — 模拟模式' },
      { type: 'output', text: '' },
      { type: 'info', text: `已连接到: ${context?.name || 'unknown'} (${context?.host || 'unknown'})` },
      { type: 'output', text: `用户: ${context?.username || 'root'} · 端口: ${context?.ssh_port || 22}` },
      { type: 'output', text: '' },
      { type: 'output', text: '输入 <span style="color:#10b981">help</span> 查看可用命令' },
      { type: 'output', text: '' },
    ];
  },

  helpText: `可用命令:\n  ls          - 列出目录\n  pwd         - 当前路径\n  whoami      - 当前用户\n  date        - 当前时间\n  uname -a    - 系统信息\n  uptime      - 运行时间\n  df -h       - 磁盘使用\n  free -h     - 内存使用\n  docker ps   - 容器列表\n  cat /etc/os-release - 系统版本\n  clear       - 清屏\n  ping <host> - Ping 主机\n  netstat -tlnp - 端口监听\n  top         - 进程概览\n  help        - 帮助`,

  execute(command, args, context) {
    /* Returns { output: string|null, error: string|null, clear: boolean } */
    switch (command) {
      case 'help':
        return { output: this.helpText };
      case 'ls':
        return { output: 'bin  boot  dev  etc  home  lib  lib64  media  mnt  opt  proc  root  run  sbin  srv  sys  tmp  usr  var' };
      case 'pwd':
        return { output: '/root' };
      case 'whoami':
        return { output: context?.username || 'root' };
      case 'date':
        return { output: new Date().toString() };
      case 'uname':
        return { output: 'Linux ' + (context?.host || 'opscenter') + ' 6.8.0-124-generic #124-Ubuntu SMP x86_64 GNU/Linux' };
      case 'uptime':
        return { output: ' ' + new Date().toLocaleTimeString() + ' up 42 days, 3:17, 1 user, load average: 0.15, 0.12, 0.08' };
      case 'df':
        return { output: 'Filesystem      Size  Used Avail Use% Mounted on\n/dev/vda1        49G   25G   22G  54% /\ntmpfs           3.6G     0  3.6G   0% /dev/shm' };
      case 'free':
        return { output: '              total        used        free      shared  buff/cache   available\nMem:          7.1Gi       3.8Gi       1.2Gi       256Mi       2.1Gi       3.3Gi\nSwap:            0B          0B          0B' };
      case 'docker':
        if (args[0] === 'ps') {
          const containers = context?.containers?.length ? context.containers : [
            { name: 'nginx', image: 'nginx:alpine', status: 'running', ports: '80, 443' },
            { name: 'gitea', image: 'gitea/gitea:latest', status: 'running', ports: '3000, 2222' },
            { name: 'jenkins', image: 'jenkins/jenkins:lts', status: 'running', ports: '8080' },
          ];
          let out = 'CONTAINER ID   IMAGE' + ' '.repeat(25) + 'STATUS      PORTS                  NAMES\n';
          containers.forEach(c => {
            const id = Math.random().toString(36).substring(2, 14);
            out += `${id}   ${c.image.padEnd(32)} Up ${c.status === 'running' ? '2 days' : 'Exited'}   ${(c.ports || '').padEnd(22)} ${c.name}\n`;
          });
          return { output: out };
        }
        return { output: 'Usage: docker ps' };
      case 'cat':
        if (args.join(' ').includes('os-release')) {
          return { output: 'PRETTY_NAME="Ubuntu 24.04.4 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\nVERSION="24.04.4 LTS (Noble Numbat)"\nID=ubuntu\nHOME_URL="https://www.ubuntu.com/"' };
        }
        return { output: `cat: ${args[0] || ''}: No such file or directory` };
      case 'clear':
        return { clear: true };
      case 'ping':
        if (args[0]) {
          return { output: `PING ${args[0]} (${args[0].includes('.') ? args[0] : '93.184.216.34'}) 56(84) bytes of data.\n64 bytes from ${args[0]}: icmp_seq=1 ttl=52 time=11.3 ms\n64 bytes from ${args[0]}: icmp_seq=2 ttl=52 time=11.1 ms\n--- ${args[0]} ping statistics ---\n2 packets transmitted, 2 received, 0% packet loss, time 1002ms` };
        }
        return { output: 'Usage: ping <host>' };
      case 'netstat':
        return { output: 'Active Internet connections (only servers)\nProto Recv-Q Send-Q Local Address           Foreign Address         State       PID/Program name\ntcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN      1234/sshd\ntcp        0      0 0.0.0.0:80              0.0.0.0:*               LISTEN      5678/nginx\ntcp        0      0 0.0.0.0:443             0.0.0.0:*               LISTEN      5678/nginx' };
      case 'top': {
        const cpuPct = context?.cpu || Math.floor(Math.random() * 15);
        const memPct = context?.memory || 54;
        return { output: `top - ${new Date().toLocaleTimeString()} up 42 days, 3:17, 1 user, load average: 0.15, 0.12, 0.08\nTasks: 128 total, 1 running, 127 sleeping, 0 stopped, 0 zombie\n%Cpu(s): ${cpuPct} us, 2.0 sy, 0.0 ni, ${(100 - cpuPct - 2).toFixed(1)} id, 0.0 wa, 0.0 hi, 0.2 si\nMiB Mem: 7264.0 total, 1228.0 free, 3891.0 used, 2145.0 buff/cache\nMiB Swap: 0.0 total, 0.0 free, 0.0 used. 3372.0 avail Mem` };
      }
      default:
        return { error: `bash: ${command}: command not found` };
    }
  },
};
