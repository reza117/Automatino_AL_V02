# General Info 
# Netboot.xyz it was in the VM . act as pxe boot server, and also DHCP  server


**Run Dashboard**
cd /home/reza/Projects/git/WDC/Automatino_AL_V02 && source wdc_manager_vh/bin/activate && python dashboard.py

**Run interactive_auto**
cd /home/reza/Projects/git/WDC/Automatino_AL_V02 && source wdc_manager_vh/bin/activate && python interactive_auto.py

# Config info 
netboot Ip => 192.168.122.114
ssh =>  netboot@192.168.122.114   
#pass =>netboot 
**DONE** => copy ssh key from host to netboot VM  
**DONE** => test ssh connection 



generate one  master config  file  for  password,  IPs and  .... future  needs 

1-  generate initial  impi  IPs  
    program  shows  menue  to the user  "Scan the Network for IPMI IPs" 
    use  nmap to scan network for the ipmi  ip  => asks  user the ip range . and  port  number (default will be  standard ipmi port number UDP 623)  or  an option to scan  as ping  only basics
    so  3  option ,  
        1- default UDP 623
        2- key in  custom port
        3- basic ping scan

2- generate iPMI_ip_list.csv  with  time stamp in  data folder  check against  existing  IPs  inside db  and  put reamark in the csv  and  all  data  avaiable in  db  related to that ipmi ip 

and  also list ips only   in  the terminal new ips and  existing  ips will be in  2  different  list  
and ask  user
 1-  add new ips to the  db
 2- scan again   
 3- check ipmi logim  against  predefined  user and  ips and  register the ips with  success credintiual and also  register the  failed  with reamrk  in the  db  and report the  failed  IP  list 
 3- quite the program   

3- deployment automation 
 at this  menu  verything  will have default  optin menu  with  5  second  count down  for user  input and this  5  second  will have config  variable that can be  changed  in  confgi  file  

3.0 list  avaiable  credintial success  ipmi ips for user  selection ,  each  ip  will have number reference , for user to  select  ,  single  or  multiple reference  or  a to sellect all . 
after user selection  server  starts one  IP  at  a  time ,    
3.1 send pxe set  process and get the status  for  confirmation . 

3.2 DHCP watcher logic 

Run DHCP query, to. record current available MGMT IPs, (mangament  IP  range can be  set  in  config file and can  accept  multipple  range with  name like 
    MGMT_1 "IP Range 1"
    MGMT_2 "IP Range 2"

DHCP server info can access without password  via  ssh  key => 
ssh =>  netboot@192.168.122.114 
    - Make sure the  ssh  success 
    - DHCP  server  is Dnsmasq , 
    - record the  current ips in  dm , wirh  single key that  contains the ipmi  ip  and timestamp 

3.3  send chassis power cycle and  make sure it was  success and  serveer  boots 

3.4 DHCP watcher logic  

while server rebooting, every 10 second (this  10  second  can be  set  in  confgi  file  as a  variable )run DHCP query and compare with original recorded. before server boot , against  unique  key  =>  ipmi IP + timestamp

3.5 
if new IP detected, show to the user in the terminal and record this Ip as MGMT IP (MGMT_1 or MGMT_2 basedon  range  defined in  config  file )for that specific ipmi record ,


3.5 Start ssh watcher against new  mgmt  ip  detected 
run ssh test against this new MGMT IP  detected with  following  template 
be default the target  server has the  ssh  key and  default  username is  "user"
ssh -o ConnectTimeout=4 -o ConnectionAttempts=60 user@<target_ip> 

Once the  SSH  was  success => run  10  second  count  down(this  10 second  can be  configured via  confgi  file )  for the  user to  select the  menu  if user  did not  key in  anything , got to  the next  default  menu  which is next  step  => 


3.6 
Maintain server_tracking table in SQLite for OS-side info.
Collect serial, IPMI MAC, and complete disk inventory over SSH.
Deploy wdc_bootstrap.sh and launches wipe in TMUX session.
confirms  if the  process is  started ,  
then  record the  report in  db  and  disconnect the  ssh  session . makesure Tmux  not  effected . 

##########
then shows  10  second  count down  for user  interaction (this 10  second  can be  configured in  confgi  file variable )and  moves to next  valid  ipmi ip address and same  process of  deployment  . 


