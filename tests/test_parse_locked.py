"""parse_locked.extract: the locked-email edge-vs-field table parser.

Late-slate emails (first seen France-Spain SF 2026-07-14) append a resolution
clarification AFTER the question mark on the same line ("...stoppage time)? If
both teams substitute simultaneously, ..."), which the old endswith("?") line
predicate silently dropped — 3 of that email's 15 rows (all realized wins)
vanished from the P&L until 2026-07-15.
"""

import parse_locked


def _email(rows):
    cells = ["<td>Your edge vs the field</td>"]
    for q, you, field, if_yes, if_no, swing in rows:
        cells.append(
            f"<td>{q}</td><td>{you}%</td><td>{field}%</td>"
            f"<td>{if_yes:+.2f}</td><td>{if_no:+.2f}</td><td>{swing}</td>")
    return "<html><body><table><tr>" + "</tr><tr>".join(cells) + \
        "</tr></table></body></html>"


def test_extract_plain_and_clarified_questions(tmp_path):
    rows = [
        ("Will both teams score in regulation (90 minutes + stoppage time)?",
         61, 59, 8.16, 0.74, 7),
        ("Will Spain make the first substitution of the match in regulation "
         "(90 minutes + stoppage time)? If both teams substitute "
         "simultaneously, resolves by whichever outgoing player exits the "
         "field first.", 35, 54, -38.50, 36.86, 75),
        ("Will the referee conduct an on-field review at the pitchside VAR "
         "monitor at any point in regulation (90 minutes + stoppage time) or "
         "extra time? Reviews during the penalty shootout do not count.",
         35, 42, -8.08, 19.88, 28),
    ]
    f = tmp_path / "2026-07-14_France_v_Spain.html"
    f.write_text(_email(rows), encoding="utf-8")
    got = parse_locked.extract(str(f))
    assert len(got) == 3
    assert got[1]["you"] == 35 and got[1]["field"] == 54
    assert got[1]["if_no"] == 36.86
    # full line (incl. clarification) is kept — it matches questions.text in the DB
    assert got[1]["question"].endswith("exits the field first.")


def test_extract_ignores_footer_prose(tmp_path):
    html = _email([("Will X win?", 50, 50, 1.0, 1.0, 0)]) \
        .replace("</table>", "</table><p>Field = the field's average "
                 "probability on this question. Swing = the gap.</p>")
    f = tmp_path / "x.html"
    f.write_text(html, encoding="utf-8")
    got = parse_locked.extract(str(f))
    assert len(got) == 1
