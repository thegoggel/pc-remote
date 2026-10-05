# PC Remote

A small web page for a phone or tablet. It wakes Nils's Windows gaming PC, starts the Steam client, and can turn the PC off.

The page runs on a Proxmox LXC on the home LAN. He opens it at a hostname on his own domain, at home or away. Cloudflare Tunnel carries only that page. The app asks him to sign in with Google and keeps a session cookie. Only the Google account in `ALLOWED_EMAIL` can use the page.

Wake calls the MikroTik RouterOS API. The router sends a Wake-on-LAN packet to one MAC address. The page waits until the PC answers on the LAN, then sends one authenticated request to a tiny listener that starts `steam.exe` and does nothing else.

Turn off asks for confirmation, then sends one authenticated request to that same listener. The listener shuts Windows down and does nothing else. That is a normal shutdown, not a remote shell.

The RouterOS API and the PC listener stay on the LAN. Do not put them on the tunnel, and do not forward them to an open port.

## Configure

Create `/etc/pc-remote.env` on the LXC. Start from `.env.example`. Mode `600`, owner root. Do not commit that file.

| Variable | Purpose |
| --- | --- |
| `PUBLIC_URL` | `https://wake.example.com`, no path. This hostname is the app. |
| `GOOGLE_CLIENT_ID` | OAuth web client id |
| `GOOGLE_CLIENT_SECRET` | OAuth client secret |
| `ALLOWED_EMAIL` | His Google email. Comma-separated if more than one. |
| `SESSION_SECRET` | Long random string, 32 characters or more |
| `MIKROTIK_HOST` | Router LAN address |
| `MIKROTIK_USER` | API user that can only send Wake-on-LAN |
| `MIKROTIK_PASSWORD` | That user's password |
| `MIKROTIK_INTERFACE` | Interface for `/tool wol`, often `bridge` |
| `PC_MAC` | Gaming PC MAC, `AA:BB:CC:DD:EE:FF` |
| `PC_ADDRESS` | Gaming PC LAN IPv4 address |
| `LISTENER_URL` | `http://<PC_ADDRESS>:8765/start`. Turn off uses this same host and port at `/shutdown`. |
| `LISTENER_SECRET` | Shared with the Windows listener, 16 characters or more |

Optional: `MIKROTIK_PORT` (default `8728`), `BIND_HOST` (default `127.0.0.1`), `BIND_PORT` (default `8080`), `WAKE_TIMEOUT_SECONDS` (default `180`).

`PUBLIC_URL` must be `https` so the session cookie is marked Secure. The tunnel terminates TLS. The app can still listen on HTTP at `127.0.0.1`, which is what cloudflared connects to. `http://` is accepted only for `localhost` or `127.0.0.1`.

Generate the two secrets on the LXC:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use one value for `SESSION_SECRET` and a different one for `LISTENER_SECRET`. Put the same listener secret in the Windows `config.ps1`.

## Google sign-in

In Google Cloud Console, create an OAuth client of type **Web application**.

Set the authorized redirect URI to the public callback, with his real hostname:

```text
https://wake.example.com/auth/callback
```

That must be exactly `PUBLIC_URL` plus `/auth/callback`.

On the consent screen, add the `openid` and `email` scopes. While the app is in **Testing**, add his Google account under **Test users**. Put that same address in `ALLOWED_EMAIL`.

Anyone else who opens the hostname gets the sign-in page only. A Google account that is not on the list does not get a session.

The session cookie lasts 30 days. It is `HttpOnly` and `SameSite=Lax`.

## MikroTik

On the PC, enable Wake on Magic Packet in the BIOS and on the Ethernet adapter. Give the PC a DHCP reservation or a static LAN address. Windows Fast Startup can ignore a magic packet after Shut down, so turn Fast Startup off if the machine must wake from a full shutdown.

On the router, keep the API on the LAN and limit it to the LXC:

```routeros
/ip service set api address=<lxc-ip>/32 disabled=no
/user group add name=pc-remote policy=api,test
/user add name=wol group=pc-remote password="<password>"
```

Sign in as `wol` and confirm `/tool wol interface=bridge mac=<PC_MAC>` works. If RouterOS refuses, add only the policy it names. Do not put this user in the `full` group.

Do not enable the API on the WAN. Do not add a dst-nat rule for port 8728.

## Windows listener

The listener is `windows/steam-listener.ps1`. It accepts two requests, both with `Authorization: Bearer <secret>`:

- `POST /start` starts `steam.exe` if it is not already running.
- `POST /shutdown` runs `shutdown.exe /s /t 0` and nothing else. The arguments are fixed. The request body is not a command. This is a normal Windows shutdown, not a restart and not a remote shell.

Every other request is rejected. Shutdown uses the same port as start. Do not open a second port.

It has to run in his desktop session, because Steam is a GUI program. If the PC was shut down, Windows needs to reach the desktop on its own (auto-login is the straightforward way). The page stays on **Waiting for Windows** until that listener accepts a connection.

On the PC:

1. Copy `windows/steam-listener.ps1` and `windows/config.ps1.example` to `C:\pc-remote\`.
2. Copy the example to `C:\pc-remote\config.ps1`. Set `ListenerSecret` to the same value as `LISTENER_SECRET`, and set `ListenPrefix` to `http://<PC_ADDRESS>:8765/`. Do not use `0.0.0.0`, `+`, or `localhost`.
3. In an elevated PowerShell, reserve that URL for his Windows user (once):

```powershell
netsh http add urlacl url=http://192.168.88.50:8765/ user=$env:USERDOMAIN\$env:USERNAME
```

4. Allow the port from the LXC only:

```powershell
New-NetFirewallRule -DisplayName "PC Remote Steam listener" -Direction Inbound -Profile Private -Action Allow -Protocol TCP -LocalPort 8765 -RemoteAddress 192.168.88.10
```

Replace the addresses with the PC and the LXC.

5. Task Scheduler → Create Task:
   - **Run only when user is logged on**
   - Trigger: **At log on**
   - Action: `powershell.exe`
   - Arguments: `-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\pc-remote\steam-listener.ps1`
   - Start in: `C:\pc-remote`

Do not publish port 8765. Do not add it to the Cloudflare Tunnel.

## LXC and the tunnel

Use a container on the home LAN (or a VLAN that can reach the router API and the PC). It needs outbound HTTPS to Google and to Cloudflare. It does not need an inbound port.

```sh
apt update
apt install -y python3 ca-certificates
useradd --system --create-home --home-dir /var/lib/pc-remote pc-remote
mkdir -p /opt/pc-remote
# Put this repo at /opt/pc-remote so /opt/pc-remote/app exists.
install -m 600 -o root -g root /dev/null /etc/pc-remote.env
# Edit /etc/pc-remote.env.
cp /opt/pc-remote/deploy/pc-remote.service /etc/systemd/system/pc-remote.service
systemctl daemon-reload
systemctl enable --now pc-remote
```

The service listens on `127.0.0.1:8080` by default, so the rest of the LAN cannot open the app directly.

Install `cloudflared` with Cloudflare's current package instructions, then create a tunnel and route DNS for the hostname (`cloudflared tunnel route dns <tunnel> wake.example.com`). The ingress file should publish the app and nothing else:

```yaml
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json

ingress:
  - hostname: wake.example.com
    service: http://127.0.0.1:8080
  - service: http_status:404
```

Leave the credentials file on the LXC. Do not commit it. Do not add an ingress rule for the router, port 8728, the PC, or port 8765.

Restart cloudflared after editing the ingress. From the phone, on Wi-Fi or on cellular, open `https://wake.example.com`.

## Using the page

Signed out, the page is only **Sign in with Google**.

After sign-in, **Wake** runs the sequence. **Turn off** opens a confirmation page. Nothing is sent until he confirms there. Cancel returns without shutting the PC down.

The heading is one of:

- **Waking** — the LXC is asking the router to send the magic packet
- **Waiting for Windows** — the packet was sent, and the page is waiting for the PC to answer
- **Steam started**
- **Failed** — the reason is under the heading

While it is waking or waiting, the page refreshes on its own. The magic packet is sent again every 30 seconds until the PC answers or about three minutes pass.

After a confirmed turn-off, the page says **The PC is turning off**, or the reason it did not. Wake does not run as part of turn-off. If the PC is already off, the listener does not answer, and the page says so.

## Tests

On a machine with Python 3.11 or newer, from this directory:

```sh
python3 -m unittest discover -s tests
```

The app uses only the Python standard library.
