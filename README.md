# Seestar Desk

Seestar Desk operates a ZWO Seestar (S30, S50, or S50 Pro) from any browser. A small Python program on your computer talks to the telescope and serves the page. Planner and weather are not in this version. Solar imaging and firmware updates stay in the official Seestar app.

You need Python 3.11 or newer, and the telescope on the same network as this computer.

## Start

From this folder:

```bash
# macOS or Linux
./seestar-desk.py

# Windows
py seestar-desk.py
```

The first start installs the Python packages if they are missing. The desk opens at http://127.0.0.1:8731/.

Useful options:

```bash
./seestar-desk.py --demo          # simulated S50 Pro, no telescope required
./seestar-desk.py --no-browser    # start the server and leave the browser closed
./seestar-desk.py --host 0.0.0.0  # reach the page from another device on your network
./seestar-desk.py --port 8731
```

Stop it with Ctrl+C in that terminal.

## Use

1. Turn the Seestar on and join its Wi-Fi, or put the telescope and this computer on the same home network.
2. Open the desk. Overview lists telescopes it finds. Choose yours and press **Connect**.
3. On firmware that still uses a control lock, press **Claim** before moving the telescope. Current S50 Pro firmware has no lock; the button can stay as it is.
4. Open a section from the left:
   - **Overview** shows link, battery, Wi-Fi, disk, and coordinates.
   - **Sky** goes to a catalog target.
   - **Picture** is live view, focus, filter, and stacking.
   - **Align** switches the mount mode. Parking powers the telescope down, so use it only when you mean to.
   - **Album** lists saved sessions. Tick the files you want, then download them. They land in `~/Pictures/Seestar Desk`.
   - **Settings** holds location, clock, dew heater, and power.
5. **Restart** and **Shut down** ask for confirmation, then power-cycle or turn off the telescope.

The desk remembers telescopes in `~/.seestar-desk/scopes.json`.

## The signature key

Current firmware ignores a client until that client signs a short challenge. The signing key is one key shared by the official Seestar app. It is the same for every Seestar. The telescope holds only the matching public key and sends a fresh challenge each time you connect. Seestar Desk does not include the key.

If the telescope says “please update to the latest application”, it has not accepted the signature yet.

The desk looks for a PEM file in this order and uses the first one it finds:

1. The path in the `SEESTAR_KEY_PATH` environment variable.
2. `seestar.pem` in the folder where you started the desk.
3. `~/.seestarpy/seestar.pem`
4. `~/.seestar-desk/seestar.pem`
5. The Seestar app on this Mac, at `/Applications/Seestar.app` or `~/Applications/Seestar.app`.

### Mac or iOS app already installed

Nothing to copy. Start the desk. It reads `my_private.pem` inside the app bundle.

To keep a copy that still works if you delete the app:

```bash
mkdir -p ~/.seestar-desk
cp "/Applications/Seestar.app/Wrapper/Seestar.app/my_private.pem" ~/.seestar-desk/seestar.pem
chmod 600 ~/.seestar-desk/seestar.pem
```

### No Seestar app on this computer

Download the official Seestar Android package (`.apk` or the APK inside an `.xapk`) from ZWO. Then, from this folder:

```bash
./seestar-desk.py --help
python3 -m seestar_desk.extract_pem path/to/seestar.apk
```

That writes `~/.seestar-desk/seestar.pem` and limits it to your user account. An `.xapk` is a zip of APKs; point the command at the inner `.apk`. If the telescope still asks you to update, repeat the command with the newest package from ZWO.

You can also place a PEM you already extracted at any of the paths above:

```bash
# macOS or Linux
export SEESTAR_KEY_PATH="$HOME/somewhere/seestar.pem"

# Windows PowerShell
$env:SEESTAR_KEY_PATH = "$HOME\somewhere\seestar.pem"
```

Do not commit the PEM, and do not send it with a bug report. `*.pem` is listed in `.gitignore`.
