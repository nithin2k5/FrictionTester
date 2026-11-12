Put .prn label template files here. They appear in the PRN Template list on the Model Settings page.

When a part passes (OK), the Test Console sends the loaded model's PRN template as a RAW job to the
Windows printer named in settings.json -> test.printer_name (default "FTM_PRINTER"). The file must
already be in the printer's language (ZPL / TSPL / EPL...). These placeholders are filled in if present:

  {MODEL} {TYPE} {PART_NUMBER} {CUSTOMER} {ALC_CODE}
  {LABEL} {LABEL_SELECT} {LABEL_SHIFT} {VERDICT}
  {DATE} {TIME} {DATETIME} {YYYYMMDD} {HHMMSS}
