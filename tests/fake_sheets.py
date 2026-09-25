"""In-memory stand-in for the Google Sheets REST API (values get/put/append/batchUpdate, addSheet)."""
import re
import urllib.parse


class Resp:
    def __init__(self, code=200, data=None, text=''):
        self.status_code, self._d, self.text = code, data or {}, text

    def json(self):
        return self._d


class FakeSheets:
    def __init__(self, tabs=None):
        self.tabs = tabs if tabs is not None else {}
        self.calls = 0

    def _split(self, url):
        rng = urllib.parse.unquote(url.split('/values/')[1].split('?')[0].split(':append')[0])
        name, cells = rng.rsplit('!', 1)
        return name.strip("'"), cells

    def get(self, url, timeout=None):
        self.calls += 1
        if '/values/' not in url:
            return Resp(200, {'properties': {'title': 'Fake'},
                              'sheets': [{'properties': {'title': t, 'gridProperties': {'rowCount': 1000, 'columnCount': 26}}}
                                         for t in self.tabs]})
        name, cells = self._split(url)
        if name not in self.tabs:
            return Resp(400, text='Unable to parse range')
        rows = self.tabs[name]
        if cells == '1:1':
            return Resp(200, {'values': rows[:1]})
        if cells in ('A:A', 'A1:A1'):
            col = [[r[0]] if r else [] for r in rows]
            return Resp(200, {'values': col[:1] if cells == 'A1:A1' else col})
        m = re.match(r'([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$', cells)
        if m and not m.group(3):                          # single cell like B4
            col = ord(m.group(1)) - 65
            r = int(m.group(2)) - 1
            v = rows[r][col] if r < len(rows) and col < len(rows[r]) else None
            return Resp(200, {'values': [[v]] if v is not None else []})
        return Resp(200, {'values': rows})

    def put(self, url, json=None, timeout=None):
        self.calls += 1
        name, cells = self._split(url)
        n = int(re.sub(r'\D', '', cells))
        rows = self.tabs[name]
        while len(rows) < n:
            rows.append([])
        rows[n - 1] = list(json['values'][0])
        return Resp()

    def post(self, url, json=None, timeout=None):
        self.calls += 1
        if url.endswith(':batchUpdate') and '/values:' not in url:
            self.tabs[json['requests'][0]['addSheet']['properties']['title']] = []
            return Resp()
        if url.endswith('/values:batchUpdate'):
            for d in json['data']:
                name, cells = d['range'].rsplit('!', 1)
                n = int(re.sub(r'\D', '', cells))
                rows = self.tabs[name.strip("'")]
                while len(rows) < n:
                    rows.append([])
                rows[n - 1] = list(d['values'][0])
            return Resp()
        name, _ = self._split(url)
        self.tabs[name].extend(list(r) for r in json['values'])
        return Resp()
