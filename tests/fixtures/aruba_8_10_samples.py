# Copyright 2026 MixasikM (https://github.com/MixasikM/ha-aruba-instant)
# SPDX-License-Identifier: Apache-2.0
"""Sanitized Aruba Instant / AOS CLI & REST payload fixtures for unit testing."""

# ---------------------------------------------------------------------------
# InstantOS 8.10+ Virtual Controller cluster (REST /rest/show-cmd format)
# ---------------------------------------------------------------------------

SHOW_SUMMARY_OUTPUT = """cli output: 

COMMAND=show summary

Name                 :MainCluster
System Location      :HQ
Domain               :DE
VC IP Address        :192.0.2.10
VC IPv6 Address      :::
IP Mode              :v4-only
Managed Via          :Local
Band                 :5.0
Conductor IP Address   *:192.0.2.11
IP Address           :192.0.2.11
Version              :8.10.0.24_97188
"""

# InstantOS 8.4 - 8.9 uses "Master IP Address" instead of "Conductor IP Address"
SHOW_SUMMARY_8_6_OUTPUT = """cli output: 

COMMAND=show summary

Name                 :BranchCluster
System Location      :Annex
Domain               :US
VC IP Address        :198.51.100.10
VC IPv6 Address      :::
IP Mode              :v4-only
Managed Via          :Local
Band                 :5.0
Master IP Address      *:198.51.100.11
IP Address           :198.51.100.11
Version              :8.6.0.19_85123
"""

SHOW_NETWORK_OUTPUT = """cli output: 

COMMAND=show network

Networks
--------
Profile Name      ESSID             Clients  Type      Band         Authentication Method  Key Management  IP Assignment  Status   Zone  Coding   Active  Max IPv4 Users
------------      -----             -------  ----      ----         ---------------------  --------------  -------------  ------   ----  ------   ------  --------------
CorpWiFi          CorpWiFi          4        employee  2.4ghz,5ghz  None                   WPA2-AES        Default VLAN   Enabled  -     Default  Yes     N/A
CorpWiFi.2G       CorpWiFi.2G       1        employee  2.4ghz       None                   WPA2-AES        Default VLAN   Enabled  -     Default  Yes     N/A
Guest WiFi        Guest WiFi        0        guest     2.4ghz,5ghz  None                   WPA2-AES        VLAN 20        Enabled  -     Default  Yes     N/A
IoTWiFi           IoTWiFi           2        employee  2.4ghz,5ghz  None                   WPA2-AES        VLAN 92        Enabled  -     Default  Yes     N/A
"""

SHOW_APS_OUTPUT = """cli output: 

COMMAND=show aps

3 Access Points
---------------
Name         IP Address    Mode    Spectrum  Clients  Type         IPv6 Address  Mesh Role  Zone  Serial #    radio0 Channel  radio0 Power (dBm)  radio0 Utilization (%)  radio0 Noise Floor (dBm)  radio1 Channel  radio1 Power (dBm)  radio1 Utilization (%)  radio1 Noise Floor (dBm)  radio2 Channel  radio2 Power (dBm)  radio2 Utilization (%)  radio2 Noise Floor (dBm)  Need Antenna Config  From Port  Config Id  Config Csum  Ext SSID Active  Age          Link Local IP Address  Uplink Port  Hierarchy Mode  Crash Info
----         ----------    ----    --------  -------  ----         ------------  ---------  ----  --------    --------------  ------------------  ----------------------  ------------------------  --------------  ------------------  ----------------------  ------------------------  --------------  ------------------  ----------------------  ------------------------  -------------------  ---------  ---------  -----------  ---------------  ---          ---------------------  -----------  --------------  ----------
AP-Lobby     192.0.2.11*   access  disable   3        635(indoor)  --            Portal     -     SN00000001  36E             18                  3(good)                 -92(good)                 1               9                   27(good)                -94(good)                 37w             15                  8(good)                 -95(good)                 No                   none       13         65476        disable          14d:3h:22m:10s --                    eth0         conductor       No
AP-Lab Desk  192.0.2.12    access  disable   3        515(indoor)  --            Portal     -     SN00000002  116E            18                  2(good)                 -91(good)                 5               9                   21(good)                -96(good)                 -               -                   -                       -                         No                   none       13         65476        disable          2d:4h:4m     --                     eth0         member          No
AP-Floor2    192.0.2.13    access  disable   1        505(indoor)  --            Portal     -     SN00000003  52E             18                  4(good)                 -93(good)                 11              9                   19(good)                -95(good)                 -               -                   -                       -                         No                   none       13         65476        disable          45s          --                     eth0         member          No
"""

# Single standalone AP header ("1 Access Point" singular) and raw SSH PTY output (CRLF, echoed command, trailing prompt)
SHOW_APS_SINGLE_SSH_RAW = (
    "show aps\r\n\r\n"
    "1 Access Point\r\n"
    "--------------\r\n"
    "Name           IP Address     Mode    Spectrum  Clients  Type         IPv6 Address  Mesh Role  Zone  Serial #    radio0 Channel  radio0 Power (dBm)  radio0 Utilization (%)  radio0 Noise Floor (dBm)  radio1 Channel  radio1 Power (dBm)  radio1 Utilization (%)  radio1 Noise Floor (dBm)  Need Antenna Config  From Port  Config Id  Config Csum  Ext SSID Active  Age        Link Local IP Address  Uplink Port  Hierarchy Mode  Crash Info\r\n"
    "----           ----------     ----    --------  -------  ----         ------------  ---------  ----  --------    --------------  ------------------  ----------------------  ------------------------  --------------  ------------------  ----------------------  ------------------------  -------------------  ---------  ---------  -----------  ---------------  ---        ---------------------  -----------  --------------  ----------\r\n"
    "AP-Standalone  198.51.100.21* access  disable   1        515(indoor)  --            Portal     -     SN00000099  100S            18                  6(good)                 -92(good)                 6               12                  14(good)                -95(good)                 No                   none       1          12345        disable          1h:12m:3s  --                     eth0         conductor       No\r\n"
    "AP-Standalone# "
)

SHOW_CLIENTS_OUTPUT = """cli output: 

COMMAND=show clients

Client List
-----------
Name                               IP Address    MAC Address        OS        ESSID       Access Point  Channel  Type     Role        IPv6 Address              Signal    Speed (mbps)
----                               ----------    -----------        --        -----       ------------  -------  ----     ----        ------------              ------    ------------
Alice-Phone                        192.0.2.101   aa:bb:cc:11:22:33  Linux     CorpWiFi    AP-Lab Desk   116E     AC       CorpWiFi    2001:db8::101             42(good)  650(good)
Bob S26 Tablet                     192.0.2.102   aa:bb:cc:11:22:44  Android   CorpWiFi.2G AP-Floor2     11       GN       CorpWiFi.2G --                        35(good)  72(good)
Carol MacBook Pro                  192.0.2.103   aa:bb:cc:11:22:55  Mac OS X  CorpWiFi    AP-Lobby      37w      6GHz-HE  CorpWiFi    2001:db8::103             48(good)  1200(good)
android-dhcp-very-long-hostname-01 192.0.2.104   aa:bb:cc:11:22:66  Android   Guest WiFi  AP-Lobby      36E      a-HE     Guest Role  --                        28(good)  400(good)
                                   192.0.2.201   dd:ee:ff:00:11:22  NOFP      IoTWiFi     AP-Lobby      1        GN       IoTWiFi     2001:db8::99              19(ok)    19(ok)
Wired-Dock                         192.0.2.210   dd:ee:ff:00:33:44  Linux     --          AP-Lab Desk   --       --       default     --                        --        --
Unassigned-Ghost                   0.0.0.0       dd:ee:ff:00:99:99  NOFP      CorpWiFi    AP-Lab Desk   5        GN       CorpWiFi    --                        12(poor)  1(poor)
"""

# Raw SSH PTY output for show clients (no "cli output:", includes echoed command & prompt)
SHOW_CLIENTS_SSH_RAW = (
    "show clients\r\n\r\n"
    "Client List\r\n"
    "-----------\r\n"
    "Name                 IP Address      MAC Address        OS       ESSID       Access Point   Channel  Type  Role        IPv6 Address   Signal    Speed (mbps)\r\n"
    "----                 ----------      -----------        --       -----       ------------   -------  ----  ----        ------------   ------    ------------\r\n"
    "Alice-Phone          198.51.100.101  aa:bb:cc:11:22:33  Linux    CorpWiFi    AP-Standalone  100S     AX    CorpWiFi    2001:db8::201  41(good)  866(good)\r\n"
    "AP-Standalone# "
)

# ---------------------------------------------------------------------------
# HTTPS REST API (:4343) JSON response envelopes & error payloads
# ---------------------------------------------------------------------------

REST_LOGIN_SUCCESS_JSON = {
    "Status": "Success",
    "Status-code": 0,
    "sid": "test-session-token-0001",
    "uid-aruba": "test-session-token-0001",
}

REST_LOGIN_EXPIRED_JSON = {
    "Status": "Failed",
    "Status-code": 1,
    "message": "Invalid session id or session id has expired",
}

REST_SHOW_CMD_ENVELOPE_JSON = {
    "Status": "Success",
    "Status-code": 0,
    "Command output": SHOW_SUMMARY_OUTPUT,
}

REST_503_DISABLED_BODY = "REST API Service is not enabled"
REST_503_SESSION_LIMIT_BODY = "Maximum number of concurrent REST sessions reached"

# ---------------------------------------------------------------------------
# Multi-AP Standalone / AOS 10 fixtures (overlapping roaming client merge)
# ---------------------------------------------------------------------------

SHOW_SUMMARY_STANDALONE_AOS10 = """cli output: 

COMMAND=show summary

Name                 :AP-Standalone-1
System Location      :Floor1
Domain               :US
Managed Via          :Central
Band                 :5.0
IP Address           :198.51.100.31
Version              :10.6.0.2_89001
"""

MULTI_AP_NODE1_CLIENTS = """show clients

Client List
-----------
Name         IP Address      MAC Address        OS       ESSID     Access Point     Channel  Type  Role      IPv6 Address  Signal    Speed (mbps)
----         ----------      -----------        --       -----     ------------     -------  ----  ----      ------------  ------    ------------
Alice-Phone  0.0.0.0         aa:bb:cc:11:22:33  Linux    CorpWiFi  AP-Standalone-1  36E      AX    CorpWiFi  --            14(poor)  65(ok)
"""

MULTI_AP_NODE2_CLIENTS = """show clients

Client List
-----------
Name         IP Address      MAC Address        OS       ESSID     Access Point     Channel  Type  Role      IPv6 Address   Signal    Speed (mbps)
----         ----------      -----------        --       -----     ------------     -------  ----  ----      ------------   ------    ------------
Alice-Phone  198.51.100.120  aa:bb:cc:11:22:33  Linux    CorpWiFi  AP-Standalone-2  100E     AX    CorpWiFi  2001:db8::301  44(good)  866(good)
"""

# ---------------------------------------------------------------------------
# InstantOS firmware, upgrade status, and swarm.cgi XML fixtures (sanitized)
# ---------------------------------------------------------------------------

SHOW_IMAGE_VERSION_OUTPUT = """cli output: 

COMMAND=show image version

Primary Partition                 :1
Primary Partition Build Time      :2026-09-26 14:26:57 UTC
Primary Partition Build Version   :8.10.0.24_97188 LSR (Digitally Signed - Production Build)
Backup Partition                  :0
Backup Partition Build Time       :2025-12-9 15:57:38 UTC
Backup Partition Build Version    :8.10.0.21_94501 LSR (Digitally Signed - Production Build)
AP Images Classes
-----------------
Class
-----
Hercules
"""

SHOW_VERSION_OUTPUT = """cli output: 

COMMAND=show version
Aruba Operating System Software.
ArubaOS (MODEL: 325), Version 8.10.0.24 LSR
Website: http://www.arubanetworks.com
(c) Copyright 2026 Hewlett Packard Enterprise Development LP.
Compiled on 2026-09-26 at 14:26:57 UTC (build 97188) by jenkins
FIPS Mode :disabled

AP uptime is 2 days 1 hour 42 minutes 32 seconds
Reboot Time and Cause: AP rebooted Sun Nov 22 21:46:35 UTC 2026; System cmd at uptime 50D 3H 10M 48S: Image Upgrade Successful
"""

SHOW_UPGRADE_INFO_OUTPUT = """cli output: 

COMMAND=show upgrade info

swarm upgrade status
--------------------
Mac                IP Address   Seed AP  AP Class  Status    Image Info  Error Detail
---                ----------   -------  --------  ------    ----------  ------------
02:00:00:10:00:01  192.0.2.11   Yes      Hercules  image-ok  From Seed   none
02:00:00:10:00:02  192.0.2.12   No       Hercules  image-ok  From Seed   none
02:00:00:10:00:03  192.0.2.13   No       Hercules  image-ok  From Seed   none
Auto reboot           :enable
Use external URL      :disable
Conductor wait Time   :0 secs 0 count
Switch Partition      :enable
Upgrade in process    :No
UAP convert process   :No
"""

SHOW_ALLOWED_APS_OUTPUT = """cli output: 

COMMAND=show allowed-aps

Allow New APs  :enable
AP Allowlist
------------
MAC Address
-----------
02:00:00:10:00:01
02:00:00:10:00:02
02:00:00:10:00:03
"""

IMAGE_CHECK_ERROR_XML = (
    "<?xml version='1.0'?><re>"
    '<data name="Image Server State   " pn="true">error</data>'
    '<data name="Image Server Message " pn="true">Internal error 001 code 2</data>'
    '<data name="New Image Version    " pn="true"></data>'
    '<data name="New Image URL        " pn="true"></data>'
    '<data name="New DRT Version      " pn="true">1.0_97611</data>'
    '<data name="New DRT URL          " pn="true">https://example.invalid/drt</data>'
    "</re>"
)

IMAGE_CHECK_SUCCESS_EMPTY_XML = (
    "<?xml version='1.0'?><re>"
    '<data name="Image Server State   " pn="true">success</data>'
    '<data name="Image Server Message " pn="true">Success</data>'
    '<data name="New Image Version    " pn="true"></data>'
    '<data name="New Image URL        " pn="true"></data>'
    "</re>"
)

IMAGE_CHECK_UPDATE_XML = (
    "<?xml version='1.0'?><re>"
    '<data name="Image Server State   " pn="true">success</data>'
    '<data name="Image Server Message " pn="true">New version available</data>'
    '<data name="New Image Version    " pn="true">8.10.0.25_98000</data>'
    '<data name="New Image URL        " pn="true">http://192.0.2.50/ArubaInstant_Hercules_8.10.0.25_98000</data>'
    "</re>"
)


