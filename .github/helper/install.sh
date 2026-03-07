#!/bin/bash

set -e

# Suppress pip root user warning and set CI mode
export PIP_ROOT_USER_ACTION=ignore
export CI=Yes

cd ~ || exit

sudo apt-get update
sudo apt-get remove -y mysql-server mysql-client || true
sudo apt-get install -y libcups2-dev redis-server mariadb-client

pip install --upgrade pip
pip install frappe-bench

mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL character_set_server = 'utf8mb4'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE DATABASE IF NOT EXISTS test_frappe"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE USER IF NOT EXISTS 'test_frappe'@'localhost' IDENTIFIED BY 'test_frappe'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "GRANT ALL PRIVILEGES ON \`test_frappe\`.* TO 'test_frappe'@'localhost'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "FLUSH PRIVILEGES"

bench init --skip-assets --python "$(which python)" --frappe-branch version-15 frappe-bench --ignore-exist

mkdir -p ~/frappe-bench/sites/test_site
cp -r "${GITHUB_WORKSPACE}/.github/helper/site_config.json" ~/frappe-bench/sites/test_site/

install_whktml() {
    wget -O /tmp/wkhtmltox.tar.xz https://github.com/frappe/wkhtmltopdf/raw/master/wkhtmltox-0.12.3_linux-generic-amd64.tar.xz
    tar -xf /tmp/wkhtmltox.tar.xz -C /tmp
    sudo mv /tmp/wkhtmltox/bin/wkhtmltopdf /usr/local/bin/wkhtmltopdf
    sudo chmod o+x /usr/local/bin/wkhtmltopdf
}
install_whktml &

cd ~/frappe-bench || exit

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile

bench get-app frappe_vault "${GITHUB_WORKSPACE}" --skip-assets
bench setup requirements --dev
bench use test_site

bench start &> bench_run_logs.txt &
CI=Yes bench build --app frappe &

bench --site test_site reinstall --yes
bench --site test_site install-app frappe_vault
