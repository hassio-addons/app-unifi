# Third-party components

This app's own scripts and configuration are MIT licensed (see the repository
`LICENSE.md`). The image also contains third-party software under its own terms:

- **UniFi OS Server installer** (`/opt/unifi-os/installer`), downloaded unmodified
  from Ubiquiti and installed at first start. Ubiquiti's software, including the
  UniFi OS Server and UniFi Network applications it installs, is licensed under
  Ubiquiti's End User License Agreement: <https://www.ui.com/legal/eula/>.
- **MongoDB 3.6** (`/opt/unifi-legacy-mongo`), copied unmodified from the previous
  version of this app so that existing UniFi Network databases can be read. Its
  licence and the copyright notices of the bundled Ubuntu 18.04 libraries are in
  `/opt/unifi-legacy-mongo/licenses`.
- **MongoDB 4.0, 4.2 and 4.4** (`/opt/unifi-mongo/<version>`), official MongoDB
  release archives used only to upgrade an existing database's compatibility
  version before the vendor import. Each directory contains the licence files from
  its archive; copyright notices for the bundled Ubuntu 18.04 libraries are in
  `/opt/unifi-mongo/licenses`.
- **Ubuntu 26.04 packages** installed with apt. Their copyright notices are in
  `/usr/share/doc/<package>/copyright`.
