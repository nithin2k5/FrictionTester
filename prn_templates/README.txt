Put .prn label template files here. They appear in the PRN Template list on the Model Settings page.

Labels print only when both cables pass. AT: two labels (SELECT, then SHIFT) after the SHIFT test.
MT: one label for the part. The Test Console sends the loaded model's PRN template as a RAW job to the
Windows printer named in settings.json -> test.printer_name (default "FTM_PRINTER"). The file must
already be in the printer's language (ZPL / TSPL / EPL...). These placeholders are filled in if present:

  {MODEL} {TYPE} {PART_NUMBER} {CUSTOMER} {ALC_CODE}
  {LABEL} {LABEL_SELECT} {LABEL_SHIFT} {VERDICT}
  {TRACE} {TRACE_SELECT} {TRACE_SHIFT}   traceability code from the scanned label
  {CABLE}                                SELECT / SHIFT (AT), BOTH (MT)
  {DATE} {TIME} {DATETIME} {YYYYMMDD} {HHMMSS}
