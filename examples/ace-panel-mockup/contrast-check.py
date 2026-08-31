#!/usr/bin/env python3
"""Final ACE panel token set. Emits the markdown contrast tables verbatim for the doc,
so the published numbers cannot drift from the values actually shipped in the CSS."""


def lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def lum(h):
    h = h.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def cr(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


DARK = {
    '--bg': '#0E1116', '--surface-1': '#151A21', '--surface-2': '#1C222B',
    '--surface-3': '#242C37', '--divider': '#2A333F', '--border': '#677689',
    '--focus': '#5EC8D8', '--text': '#E7ECF3', '--text-dim': '#A6B2C4',
    '--text-faint': '#8B93A1', '--ok': '#4ED88F', '--staged': '#F5B942',
    '--busy': '#7FA9F5', '--fault': '#FF6B6B', '--unknown': '#98A3B5',
    '--destructive': '#FF6B6B', '--accent': '#5EC8D8',
}
LIGHT = {
    '--bg': '#EEF1F6', '--surface-1': '#FFFFFF', '--surface-2': '#F4F7FB',
    '--surface-3': '#E7ECF3', '--divider': '#D3DBE6', '--border': '#7B899B',
    '--focus': '#0B6E7C', '--text': '#141A22', '--text-dim': '#4C5A6C',
    '--text-faint': '#616B79', '--ok': '#0E7A4A', '--staged': '#8A5600',
    '--busy': '#2B5FC9', '--fault': '#B92626', '--unknown': '#5E6B7D',
    '--destructive': '#B92626', '--accent': '#0B6E7C',
}

SURF = ['--bg', '--surface-1', '--surface-2', '--surface-3']
INK = ['--text', '--text-dim', '--text-faint', '--ok', '--staged',
       '--busy', '--fault', '--unknown', '--accent']


def table(name, T):
    print('#### %s' % name)
    print()
    print('| ink | hex | on `--bg` | on `--surface-1` | on `--surface-2` | on `--surface-3` | worst | AA body |')
    print('|---|---|---|---|---|---|---|---|')
    for k in INK:
        vals = [cr(T[k], T[s]) for s in SURF]
        w = min(vals)
        print('| `%s` | `%s` | %.2f | %.2f | %.2f | %.2f | **%.2f** | %s |'
              % (k, T[k], vals[0], vals[1], vals[2], vals[3], w,
                 'pass' if w >= 4.5 else 'FAIL'))
    print()
    b = T['--border']
    vals = [cr(b, T[s]) for s in SURF]
    print('| boundary | hex | on `--bg` | on `--surface-1` | on `--surface-2` | on `--surface-3` | worst | AA UI (3:1) |')
    print('|---|---|---|---|---|---|---|---|')
    print('| `--border` | `%s` | %.2f | %.2f | %.2f | %.2f | **%.2f** | %s |'
          % (b, vals[0], vals[1], vals[2], vals[3], min(vals),
             'pass' if min(vals) >= 3.0 else 'FAIL'))
    f = T['--focus']
    vals = [cr(f, T[s]) for s in SURF]
    print('| `--focus` | `%s` | %.2f | %.2f | %.2f | %.2f | **%.2f** | %s |'
          % (f, vals[0], vals[1], vals[2], vals[3], min(vals),
             'pass' if min(vals) >= 3.0 else 'FAIL'))
    d = T['--divider']
    vals = [cr(d, T[s]) for s in SURF]
    print('| `--divider` | `%s` | %.2f | %.2f | %.2f | %.2f | **%.2f** | n/a — decorative |'
          % (d, vals[0], vals[1], vals[2], vals[3], min(vals)))
    print()


table('Dark (default)', DARK)
table('Light', LIGHT)
