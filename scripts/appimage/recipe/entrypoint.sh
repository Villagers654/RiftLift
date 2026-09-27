bin="${APPDIR}/opt/python{{ python-version }}/bin"
if [ "$#" -eq 0 ]; then
  "{{ python-executable }}" "$bin/riftlift" gui
else
  "{{ python-executable }}" "$bin/riftlift" "$@"
fi
