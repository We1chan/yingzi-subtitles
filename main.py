import logging
import os
import sys
import traceback
from pathlib import Path

log_path = Path(__file__).resolve().parent / 'logs' / 'app.log'
log_path.parent.mkdir(exist_ok=True)
logging.basicConfig(filename=log_path, level=logging.INFO, encoding='utf-8', format='%(asctime)s %(levelname)s %(message)s')


def exception_handler(exc_type, exc_value, tb):
    logging.error('Unhandled application exception', exc_info=(exc_type, exc_value, tb))
    from PySide6.QtWidgets import QApplication, QMessageBox
    if QApplication.instance():
        QMessageBox.critical(None, '映字运行提示', f'{exc_value}\n\n详细日志：{log_path}')


sys.excepthook = exception_handler
from app.gui import main

if __name__ == '__main__':
    logging.info('Starting Yingzi desktop application, PID=%s', os.getpid())
    raise SystemExit(main())
