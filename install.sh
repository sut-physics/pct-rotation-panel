#!/usr/bin/env bash
# ติดตั้งสิ่งที่ gui/pct_panel.py ต้องใช้บน Ubuntu/Debian: Tkinter, pyserial, สิทธิ์เปิดพอร์ต (dialout)
set -e

echo ">> ติดตั้ง python3, python3-tk, python3-serial ด้วย apt (Ubuntu 24.04 ไม่ให้ pip install ลงระบบ)"
sudo apt update
sudo apt install -y python3 python3-tk python3-serial

if id -nG "$USER" | grep -qw dialout; then
  echo ">> $USER อยู่ในกลุ่ม dialout อยู่แล้ว"
else
  echo ">> เพิ่ม $USER เข้ากลุ่ม dialout (เปิด /dev/ttyACM0 ได้)"
  sudo usermod -aG dialout "$USER"
  echo "!! ต้อง logout แล้ว login ใหม่ 1 ครั้ง สิทธิ์ถึงจะมีผล"
fi

chmod +x "$(dirname "$0")/run.sh"
echo ">> เสร็จแล้ว เปิดโปรแกรมด้วย ./run.sh"
