"""Build THESIS_PRO.md -> THESIS_PRO.html -> THESIS_PRO.pdf.

Pure-Python Markdown->HTML (the `markdown` package), hand-authored inline SVG diagrams injected at
[[FIG-*]] tokens, a print stylesheet, then Microsoft Edge (headless) renders the PDF. Edge is used
because it is a Microsoft-signed binary (allowed under this machine's application-control policy) and
renders SVG + paged CSS faithfully.

THESIS_PRO.md is the canonical thesis; pass a different markdown file as the first argument to build
another (outputs share its stem).

Run with the signed interpreter:
    & $py build_thesis_pdf.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
# Optional CLI arg: the markdown file to build (default THESIS_PRO.md). Outputs share its stem.
_NAME = sys.argv[1] if len(sys.argv) > 1 else "THESIS_PRO.md"
MD = (HERE / _NAME).resolve()
HTML = MD.with_suffix(".html")
PDF = MD.with_suffix(".pdf")

# ---------------------------------------------------------------------------
# Hand-authored SVG figures. Each is justified in the text: it shows something
# that is genuinely clearer as a picture than as prose (geometry, a load path,
# a bipartite matching, a moment diagram) — not decoration.
# Palette: ink #1a2b3c, steel #2c5f7c, steel-light #e8f0f5, green #1e8449,
# amber #d98a2b, red #c0392b, grey #8a97a3.
# ---------------------------------------------------------------------------

FIG_CARBON = """
<figure class="fig" style="max-width:500px">
<svg viewBox="0 0 560 235" role="img" aria-label="Embodied carbon: new steel 1.55, reuse process 0.10, saved 1.45 kgCO2e per kg">
  <text x="280" y="22" text-anchor="middle" font-size="14" font-weight="bold" fill="#1a2b3c">Carbon per kilogram of steel</text>
  <text x="178" y="62" text-anchor="end" font-size="13" fill="#1a2b3c">Buy new (A1&#8211;A3)</text>
  <rect x="188" y="46" width="300" height="26" fill="#2c5f7c"/>
  <text x="496" y="64" font-size="12.5" font-weight="bold" fill="#1a2b3c">1.55</text>
  <text x="178" y="112" text-anchor="end" font-size="13" fill="#1a2b3c">Reuse process</text>
  <rect x="188" y="96" width="19.4" height="26" fill="#8aa9bd"/>
  <text x="215" y="114" font-size="12.5" font-weight="bold" fill="#1a2b3c">0.10</text>
  <text x="178" y="162" text-anchor="end" font-size="13" fill="#1a2b3c">Saved by reusing</text>
  <rect x="188" y="146" width="280.6" height="26" fill="#1e8449"/>
  <text x="476" y="164" font-size="12.5" font-weight="bold" fill="#1a2b3c">1.45</text>
  <line x1="188" y1="42" x2="188" y2="180" stroke="#cfd8df" stroke-width="1"/>
  <text x="188" y="206" font-size="11.5" fill="#5a6b78">kgCO&#8322;e per kg &#8212; reusing a member avoids almost all of the 1.55 production carbon.</text>
</svg>
<figcaption><b>Figure 1.</b> Why reuse helps: making new steel costs ~1.55 kgCO&#8322;e/kg, but reusing a
member costs only ~0.10, so each reused kilogram saves ~1.45.</figcaption>
</figure>
"""

FIG_SECTION = """
<figure class="fig" style="max-width:360px">
<svg viewBox="0 0 400 415" role="img" aria-label="Anatomy of an I or H steel section with its dimensions and axes">
  <!-- flanges + web -->
  <rect x="120" y="90"  width="160" height="22" fill="#cfe0ea" stroke="#2c5f7c" stroke-width="1.5"/>
  <rect x="120" y="308" width="160" height="22" fill="#cfe0ea" stroke="#2c5f7c" stroke-width="1.5"/>
  <rect x="190" y="112" width="20"  height="196" fill="#cfe0ea" stroke="#2c5f7c" stroke-width="1.5"/>
  <!-- centroidal axes -->
  <line x1="92"  y1="210" x2="308" y2="210" stroke="#c0392b" stroke-width="1.2" stroke-dasharray="7 4"/>
  <line x1="200" y1="72"  x2="200" y2="348" stroke="#1e8449" stroke-width="1.2" stroke-dasharray="7 4"/>
  <text x="86"  y="214" text-anchor="end"  font-size="13" fill="#c0392b" font-weight="bold">y</text>
  <text x="311" y="205" font-size="13" fill="#c0392b" font-weight="bold">y</text>
  <text x="209" y="86"  text-anchor="start" font-size="13" fill="#1e8449" font-weight="bold">z</text>
  <text x="196" y="360" text-anchor="end" font-size="13" fill="#1e8449" font-weight="bold">z</text>
  <!-- h dimension (right) -->
  <line x1="322" y1="90" x2="322" y2="330" stroke="#1a2b3c" stroke-width="1"/>
  <line x1="317" y1="90" x2="327" y2="90" stroke="#1a2b3c" stroke-width="1"/>
  <line x1="317" y1="330" x2="327" y2="330" stroke="#1a2b3c" stroke-width="1"/>
  <text x="336" y="214" font-size="13" fill="#1a2b3c">h</text>
  <!-- b dimension (top) -->
  <line x1="120" y1="62" x2="280" y2="62" stroke="#1a2b3c" stroke-width="1"/>
  <line x1="120" y1="57" x2="120" y2="67" stroke="#1a2b3c" stroke-width="1"/>
  <line x1="280" y1="57" x2="280" y2="67" stroke="#1a2b3c" stroke-width="1"/>
  <text x="200" y="52" text-anchor="middle" font-size="13" fill="#1a2b3c">b</text>
  <!-- t_f -->
  <line x1="108" y1="90" x2="108" y2="112" stroke="#1a2b3c" stroke-width="1"/>
  <text x="103" y="106" text-anchor="end" font-size="12" fill="#1a2b3c">t</text>
  <text x="106" y="110" font-size="9" fill="#1a2b3c">f</text>
  <!-- t_w leader -->
  <line x1="210" y1="250" x2="284" y2="250" stroke="#1a2b3c" stroke-width="0.8"/>
  <text x="288" y="254" font-size="12" fill="#1a2b3c">t</text>
  <text x="296" y="258" font-size="9" fill="#1a2b3c">w</text>
  <!-- r leader -->
  <line x1="190" y1="116" x2="150" y2="140" stroke="#1a2b3c" stroke-width="0.8"/>
  <text x="120" y="140" font-size="12" fill="#1a2b3c">r</text>
  <text x="200" y="386" text-anchor="middle" font-size="11.5" fill="#5a6b78">y&#8211;y = strong (major) axis &#183; z&#8211;z = weak (minor) axis</text>
  <text x="200" y="403" text-anchor="middle" font-size="11.5" fill="#5a6b78">bending is easy about y; buckling is dangerous about z</text>
</svg>
<figcaption><b>Figure 2.</b> The parts of a rolled I/H section. Every symbol in the Eurocode formulas
(h, b, t<sub>f</sub>, t<sub>w</sub>, r, and the y/z axes) refers to this picture.</figcaption>
</figure>
"""

FIG_FAILURE = """
<figure class="fig" style="max-width:600px">
<svg viewBox="0 0 620 360" role="img" aria-label="Four steel failure modes: compression buckling, bending, lateral-torsional buckling, shear">
  <defs>
    <marker id="afA" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#c0392b"/></marker>
    <marker id="afB" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#2c5f7c"/></marker>
  </defs>
  <!-- frames -->
  <rect x="8"   y="8"   width="296" height="166" rx="6" fill="#f7fafc" stroke="#cfd8df"/>
  <rect x="316" y="8"   width="296" height="166" rx="6" fill="#f7fafc" stroke="#cfd8df"/>
  <rect x="8"   y="186" width="296" height="166" rx="6" fill="#f7fafc" stroke="#cfd8df"/>
  <rect x="316" y="186" width="296" height="166" rx="6" fill="#f7fafc" stroke="#cfd8df"/>
  <!-- A: compression buckling -->
  <text x="156" y="30" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a2b3c">1 &#183; Compression buckling</text>
  <line x1="140" y1="60" x2="172" y2="60" stroke="#2c5f7c" stroke-width="2"/>
  <line x1="140" y1="150" x2="172" y2="150" stroke="#2c5f7c" stroke-width="2"/>
  <path d="M156 60 C 205 88 205 122 156 150" fill="none" stroke="#2c5f7c" stroke-width="3"/>
  <line x1="156" y1="60" x2="156" y2="150" stroke="#b9c6d0" stroke-width="1" stroke-dasharray="4 4"/>
  <line x1="156" y1="44" x2="156" y2="59" stroke="#c0392b" stroke-width="2" marker-end="url(#afA)"/>
  <line x1="156" y1="166" x2="156" y2="151" stroke="#c0392b" stroke-width="2" marker-end="url(#afA)"/>
  <text x="214" y="108" font-size="11" fill="#5a6b78">bows sideways</text>
  <!-- B: bending -->
  <text x="464" y="30" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a2b3c">2 &#183; Bending</text>
  <text x="464" y="58" text-anchor="middle" font-size="10.5" fill="#c0392b">top fibres squashed</text>
  <path d="M356 96 Q 464 128 572 96" fill="none" stroke="#2c5f7c" stroke-width="3"/>
  <path d="M356 96 L 348 112 L 364 112 Z" fill="#8a97a3"/>
  <path d="M572 96 L 564 112 L 580 112 Z" fill="#8a97a3"/>
  <line x1="380" y1="66" x2="380" y2="86" stroke="#2c5f7c" stroke-width="1.6" marker-end="url(#afB)"/>
  <line x1="420" y1="66" x2="420" y2="88" stroke="#2c5f7c" stroke-width="1.6" marker-end="url(#afB)"/>
  <line x1="464" y1="66" x2="464" y2="90" stroke="#2c5f7c" stroke-width="1.6" marker-end="url(#afB)"/>
  <line x1="508" y1="66" x2="508" y2="88" stroke="#2c5f7c" stroke-width="1.6" marker-end="url(#afB)"/>
  <line x1="548" y1="66" x2="548" y2="86" stroke="#2c5f7c" stroke-width="1.6" marker-end="url(#afB)"/>
  <text x="464" y="150" text-anchor="middle" font-size="10.5" fill="#1e8449">bottom fibres stretched</text>
  <!-- C: LTB -->
  <text x="156" y="208" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a2b3c">3 &#183; Lateral&#8211;torsional buckling</text>
  <g stroke="#2c5f7c" stroke-width="2.4" fill="none">
    <line x1="60" y1="238" x2="100" y2="238"/><line x1="60" y1="300" x2="100" y2="300"/><line x1="80" y1="238" x2="80" y2="300"/>
  </g>
  <g transform="rotate(24 230 270)" stroke="#c0392b" stroke-width="2.4" fill="none">
    <line x1="208" y1="240" x2="252" y2="240"/><line x1="208" y1="300" x2="252" y2="300"/><line x1="230" y1="240" x2="230" y2="300"/>
  </g>
  <path d="M118 252 C 160 236 186 246 196 258" fill="none" stroke="#8a97a3" stroke-width="1.6" marker-end="url(#afB)"/>
  <text x="156" y="338" text-anchor="middle" font-size="10.5" fill="#5a6b78">a tall beam rolls over &amp; twists sideways</text>
  <!-- D: shear -->
  <text x="464" y="208" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a2b3c">4 &#183; Shear</text>
  <rect x="372" y="250" width="184" height="40" fill="#cfe0ea" stroke="#2c5f7c" stroke-width="1.5"/>
  <line x1="464" y1="238" x2="464" y2="302" stroke="#b9c6d0" stroke-width="1" stroke-dasharray="4 4"/>
  <line x1="408" y1="306" x2="408" y2="246" stroke="#c0392b" stroke-width="2.4" marker-end="url(#afA)"/>
  <line x1="520" y1="234" x2="520" y2="294" stroke="#c0392b" stroke-width="2.4" marker-end="url(#afA)"/>
  <text x="464" y="338" text-anchor="middle" font-size="10.5" fill="#5a6b78">the web is sliced near the supports</text>
</svg>
<figcaption><b>Figure 3.</b> The four ways a steel member can fail, each checked by Eurocode (Chapter 7).
These are far easier to picture than to describe in words.</figcaption>
</figure>
"""

FIG_PIPELINE = """
<figure class="fig" style="max-width:470px">
<svg viewBox="0 0 600 632" role="img" aria-label="The tool's pipeline from building models to the final report">
  <defs>
    <marker id="apD" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#2c5f7c"/></marker>
  </defs>
  <!-- inputs -->
  <rect x="40"  y="16" width="180" height="52" rx="6" fill="#e8f0f5" stroke="#2c5f7c"/>
  <text x="130" y="40" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a2b3c">Donor model</text>
  <text x="130" y="57" text-anchor="middle" font-size="10.5" fill="#5a6b78">old building (Revit/IFC)</text>
  <rect x="380" y="16" width="180" height="52" rx="6" fill="#e8f0f5" stroke="#2c5f7c"/>
  <text x="470" y="40" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a2b3c">Demand model</text>
  <text x="470" y="57" text-anchor="middle" font-size="10.5" fill="#5a6b78">new design (Revit/IFC)</text>
  <line x1="130" y1="68" x2="130" y2="106" stroke="#2c5f7c" stroke-width="1.5" marker-end="url(#apD)"/>
  <line x1="470" y1="68" x2="470" y2="106" stroke="#2c5f7c" stroke-width="1.5" marker-end="url(#apD)"/>
  <text x="300" y="92" text-anchor="middle" font-size="10.5" fill="#5a6b78">extractor reads the model &#8594; JSON</text>
  <rect x="40"  y="108" width="180" height="34" rx="5" fill="#fff" stroke="#8a97a3"/>
  <text x="130" y="130" text-anchor="middle" font-size="12" fill="#1a2b3c">donor.json</text>
  <rect x="380" y="108" width="180" height="34" rx="5" fill="#fff" stroke="#8a97a3"/>
  <text x="470" y="130" text-anchor="middle" font-size="12" fill="#1a2b3c">demand.json</text>
  <path d="M130 142 C 130 175 300 165 300 192" fill="none" stroke="#2c5f7c" stroke-width="1.5" marker-end="url(#apD)"/>
  <path d="M470 142 C 470 175 300 165 300 192" fill="none" stroke="#2c5f7c" stroke-width="1.5" marker-end="url(#apD)"/>
  <!-- main stack -->
  <g font-size="12.5" fill="#1a2b3c">
    <rect x="170" y="194" width="260" height="44" rx="6" fill="#eef3f7" stroke="#2c5f7c"/>
    <text x="300" y="221" text-anchor="middle">1 &#183; Section mapping &#160;(Ch 5)</text>
    <rect x="170" y="258" width="260" height="44" rx="6" fill="#eef3f7" stroke="#2c5f7c"/>
    <text x="300" y="285" text-anchor="middle">2 &#183; Loads &#8594; forces &#160;(Ch 6)</text>
    <rect x="170" y="322" width="260" height="44" rx="6" fill="#fdecea" stroke="#c0392b" stroke-width="2"/>
    <text x="300" y="349" text-anchor="middle">3 &#183; EN 1993-1-1 checks &#9733; &#160;(Ch 7)</text>
    <rect x="170" y="386" width="260" height="44" rx="6" fill="#eef3f7" stroke="#2c5f7c"/>
    <text x="300" y="413" text-anchor="middle">4 &#183; Carbon passport &#160;(Ch 9)</text>
    <rect x="170" y="450" width="260" height="44" rx="6" fill="#fdecea" stroke="#c0392b" stroke-width="2"/>
    <text x="300" y="477" text-anchor="middle">5 &#183; MILP matching &#9733; &#160;(Ch 10)</text>
    <rect x="170" y="514" width="260" height="44" rx="6" fill="#eef3f7" stroke="#2c5f7c"/>
    <text x="300" y="541" text-anchor="middle">6 &#183; Report + AI narrative &#160;(Ch 12)</text>
  </g>
  <g stroke="#2c5f7c" stroke-width="1.5">
    <line x1="300" y1="238" x2="300" y2="258" marker-end="url(#apD)"/>
    <line x1="300" y1="302" x2="300" y2="322" marker-end="url(#apD)"/>
    <line x1="300" y1="366" x2="300" y2="386" marker-end="url(#apD)"/>
    <line x1="300" y1="430" x2="300" y2="450" marker-end="url(#apD)"/>
    <line x1="300" y1="494" x2="300" y2="514" marker-end="url(#apD)"/>
  </g>
  <!-- side: optional frame -->
  <rect x="446" y="318" width="146" height="52" rx="6" fill="#fff" stroke="#2c5f7c" stroke-dasharray="5 4"/>
  <text x="519" y="338" text-anchor="middle" font-size="10.5" fill="#1a2b3c">Global frame</text>
  <text x="519" y="353" text-anchor="middle" font-size="10.5" fill="#1a2b3c">analysis (Ch 8)</text>
  <text x="519" y="366" text-anchor="middle" font-size="9.5" fill="#5a6b78">optional</text>
  <line x1="430" y1="344" x2="446" y2="344" stroke="#2c5f7c" stroke-width="1.2" stroke-dasharray="5 4"/>
  <!-- side: ML -->
  <rect x="446" y="446" width="146" height="52" rx="6" fill="#f3f3f3" stroke="#bbb" stroke-dasharray="5 4"/>
  <text x="519" y="466" text-anchor="middle" font-size="10.5" fill="#5a6b78">ML side-study</text>
  <text x="519" y="481" text-anchor="middle" font-size="10.5" fill="#5a6b78">(Ch 11) &#8212; beside</text>
  <text x="519" y="494" text-anchor="middle" font-size="9.5" fill="#8a97a3">not in the path</text>
  <line x1="430" y1="472" x2="446" y2="472" stroke="#bbb" stroke-width="1.2" stroke-dasharray="5 4"/>
  <!-- legend -->
  <text x="300" y="588" text-anchor="middle" font-size="11" fill="#5a6b78">&#9733; the two steps you must trust: the Eurocode checks and the optimisation.</text>
  <text x="300" y="606" text-anchor="middle" font-size="11" fill="#5a6b78">The AI writes the words; every number is computed in Python and checked.</text>
</svg>
<figcaption><b>Figure 4.</b> The whole pipeline. Two building models go in at the top; a structurally
valid, carbon-optimal set of reuse matches and a report come out at the bottom.</figcaption>
</figure>
"""

FIG_TRIB = """
<figure class="fig" style="max-width:480px">
<svg viewBox="0 0 540 420" role="img" aria-label="Plan view of a column grid showing the tributary area of a beam and a column">
  <text x="16" y="26" font-size="12" fill="#5a6b78">Plan view (looking down on the floor)</text>
  <!-- tributary fills -->
  <rect x="90"  y="70"  width="170" height="130" fill="#2c5f7c" opacity="0.16"/>
  <rect x="175" y="135" width="170" height="130" fill="#d98a2b" opacity="0.28"/>
  <!-- grid beams -->
  <g stroke="#8a97a3" stroke-width="2">
    <line x1="90" y1="70"  x2="430" y2="70"/>
    <line x1="90" y1="200" x2="430" y2="200"/>
    <line x1="90" y1="330" x2="430" y2="330"/>
    <line x1="90"  y1="70" x2="90"  y2="330"/>
    <line x1="260" y1="70" x2="260" y2="330"/>
    <line x1="430" y1="70" x2="430" y2="330"/>
  </g>
  <!-- columns -->
  <g fill="#2c5f7c">
    <circle cx="90"  cy="70"  r="5"/><circle cx="260" cy="70"  r="5"/><circle cx="430" cy="70"  r="5"/>
    <circle cx="90"  cy="200" r="5"/><circle cx="260" cy="200" r="6.5" stroke="#d98a2b" stroke-width="2"/><circle cx="430" cy="200" r="5"/>
    <circle cx="90"  cy="330" r="5"/><circle cx="260" cy="330" r="5"/><circle cx="430" cy="330" r="5"/>
  </g>
  <!-- half-bay dims on the column -->
  <line x1="175" y1="284" x2="260" y2="284" stroke="#1a2b3c" stroke-width="0.8"/>
  <line x1="260" y1="284" x2="345" y2="284" stroke="#1a2b3c" stroke-width="0.8"/>
  <text x="217" y="298" text-anchor="middle" font-size="10.5" fill="#1a2b3c">&#189; bay</text>
  <text x="302" y="298" text-anchor="middle" font-size="10.5" fill="#1a2b3c">&#189; bay</text>
  <!-- labels -->
  <rect x="300" y="96" width="150" height="38" rx="4" fill="#fff" stroke="#2c5f7c"/>
  <text x="375" y="111" text-anchor="middle" font-size="10.5" fill="#1a2b3c">beam tributary strip</text>
  <text x="375" y="125" text-anchor="middle" font-size="10.5" fill="#1a2b3c">(&#189; bay each side)</text>
  <line x1="300" y1="120" x2="180" y2="135" stroke="#2c5f7c" stroke-width="0.8"/>
  <rect x="360" y="236" width="170" height="38" rx="4" fill="#fff" stroke="#d98a2b"/>
  <text x="445" y="251" text-anchor="middle" font-size="10.5" fill="#1a2b3c">interior column tributary</text>
  <text x="445" y="265" text-anchor="middle" font-size="10.5" fill="#1a2b3c">= a quarter of each bay around it</text>
  <text x="270" y="392" text-anchor="middle" font-size="11" fill="#5a6b78">Each member carries the strip/patch of floor nearest to it. An edge beam conservatively takes the whole bay.</text>
</svg>
<figcaption><b>Figure 5.</b> Tributary area &#8212; how the floor load is shared out. The shaded strip is one
beam's load width; the shaded rectangle is one column's load area (Chapter 6).</figcaption>
</figure>
"""

FIG_BEAM = """
<figure class="fig" style="max-width:480px">
<svg viewBox="0 0 520 430" role="img" aria-label="Simply supported beam with its shear-force and bending-moment diagrams">
  <defs>
    <marker id="abD" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#2c5f7c"/></marker>
  </defs>
  <!-- 1. the beam -->
  <text x="14" y="28" font-size="12.5" font-weight="bold" fill="#1a2b3c">The beam</text>
  <line x1="70" y1="52" x2="450" y2="52" stroke="#5a6b78" stroke-width="1"/>
  <g stroke="#2c5f7c" stroke-width="1.6">
    <line x1="100" y1="54" x2="100" y2="78" marker-end="url(#abD)"/>
    <line x1="160" y1="54" x2="160" y2="78" marker-end="url(#abD)"/>
    <line x1="220" y1="54" x2="220" y2="78" marker-end="url(#abD)"/>
    <line x1="280" y1="54" x2="280" y2="78" marker-end="url(#abD)"/>
    <line x1="340" y1="54" x2="340" y2="78" marker-end="url(#abD)"/>
    <line x1="400" y1="54" x2="400" y2="78" marker-end="url(#abD)"/>
  </g>
  <text x="260" y="44" text-anchor="middle" font-size="11.5" fill="#1a2b3c">w &#8212; uniform load (per metre)</text>
  <line x1="70" y1="82" x2="450" y2="82" stroke="#2c5f7c" stroke-width="3.5"/>
  <path d="M70 82 L58 100 L82 100 Z" fill="#8a97a3"/>
  <path d="M450 82 L438 100 L462 100 Z" fill="#8a97a3"/>
  <circle cx="444" cy="104" r="3" fill="none" stroke="#8a97a3"/><circle cx="456" cy="104" r="3" fill="none" stroke="#8a97a3"/>
  <line x1="70" y1="116" x2="450" y2="116" stroke="#1a2b3c" stroke-width="0.8"/>
  <line x1="70" y1="111" x2="70" y2="121" stroke="#1a2b3c" stroke-width="0.8"/>
  <line x1="450" y1="111" x2="450" y2="121" stroke="#1a2b3c" stroke-width="0.8"/>
  <text x="260" y="132" text-anchor="middle" font-size="12" fill="#1a2b3c">span L</text>
  <!-- 2. shear -->
  <text x="14" y="198" font-size="12.5" font-weight="bold" fill="#1a2b3c">Shear force V</text>
  <line x1="70" y1="210" x2="450" y2="210" stroke="#5a6b78" stroke-width="1"/>
  <polygon points="70,210 70,170 450,250 450,210" fill="#2c5f7c" opacity="0.16"/>
  <line x1="70" y1="170" x2="450" y2="250" stroke="#2c5f7c" stroke-width="2"/>
  <text x="64" y="170" text-anchor="end" font-size="11.5" fill="#1a2b3c">+wL/2</text>
  <text x="456" y="252" font-size="11.5" fill="#1a2b3c">&#8722;wL/2</text>
  <text x="270" y="204" text-anchor="middle" font-size="10.5" fill="#5a6b78">zero at mid-span</text>
  <!-- 3. moment -->
  <text x="14" y="300" font-size="12.5" font-weight="bold" fill="#1a2b3c">Bending moment M</text>
  <line x1="70" y1="360" x2="450" y2="360" stroke="#5a6b78" stroke-width="1"/>
  <path d="M70 360 Q 260 240 450 360 Z" fill="#d98a2b" opacity="0.20"/>
  <path d="M70 360 Q 260 240 450 360" fill="none" stroke="#d98a2b" stroke-width="2.5"/>
  <text x="260" y="294" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a2b3c">M = wL&#178;/8</text>
  <text x="260" y="384" text-anchor="middle" font-size="10.5" fill="#5a6b78">greatest at mid-span</text>
</svg>
<figcaption><b>Figure 6.</b> A simply-supported beam under a uniform load, with the shear-force and
bending-moment diagrams the tool uses by default (V = wL/2, M = wL&#178;/8).</figcaption>
</figure>
"""

FIG_FRAME = """
<figure class="fig" style="max-width:520px">
<svg viewBox="0 0 560 430" role="img" aria-label="Two-bay two-storey frame showing how column forces build up along the load path">
  <defs>
    <marker id="afrD" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#2c5f7c"/></marker>
  </defs>
  <!-- beams -->
  <line x1="80" y1="70"  x2="480" y2="70"  stroke="#2c5f7c" stroke-width="5"/>
  <line x1="80" y1="200" x2="480" y2="200" stroke="#2c5f7c" stroke-width="5"/>
  <!-- columns -->
  <g stroke="#2c5f7c" stroke-width="4">
    <line x1="80"  y1="70" x2="80"  y2="340"/>
    <line x1="280" y1="70" x2="280" y2="340"/>
    <line x1="480" y1="70" x2="480" y2="340"/>
  </g>
  <!-- supports -->
  <g stroke="#1a2b3c" stroke-width="1.4">
    <line x1="60" y1="340" x2="100" y2="340"/><line x1="62" y1="340" x2="54" y2="352"/><line x1="74" y1="340" x2="66" y2="352"/><line x1="86" y1="340" x2="78" y2="352"/><line x1="98" y1="340" x2="90" y2="352"/>
    <line x1="260" y1="340" x2="300" y2="340"/><line x1="262" y1="340" x2="254" y2="352"/><line x1="274" y1="340" x2="266" y2="352"/><line x1="286" y1="340" x2="278" y2="352"/><line x1="298" y1="340" x2="290" y2="352"/>
    <line x1="460" y1="340" x2="500" y2="340"/><line x1="462" y1="340" x2="454" y2="352"/><line x1="474" y1="340" x2="466" y2="352"/><line x1="486" y1="340" x2="478" y2="352"/><line x1="498" y1="340" x2="490" y2="352"/>
  </g>
  <!-- UDL on beams -->
  <g stroke="#2c5f7c" stroke-width="1.4">
    <line x1="120" y1="52" x2="120" y2="67" marker-end="url(#afrD)"/><line x1="200" y1="52" x2="200" y2="67" marker-end="url(#afrD)"/><line x1="360" y1="52" x2="360" y2="67" marker-end="url(#afrD)"/><line x1="440" y1="52" x2="440" y2="67" marker-end="url(#afrD)"/>
    <line x1="120" y1="182" x2="120" y2="197" marker-end="url(#afrD)"/><line x1="200" y1="182" x2="200" y2="197" marker-end="url(#afrD)"/><line x1="360" y1="182" x2="360" y2="197" marker-end="url(#afrD)"/><line x1="440" y1="182" x2="440" y2="197" marker-end="url(#afrD)"/>
  </g>
  <text x="280" y="44" text-anchor="middle" font-size="11.5" fill="#5a6b78">floor load sits on the beams only</text>
  <!-- axial labels (white background for legibility) -->
  <g font-size="11" fill="#1a2b3c">
    <rect x="40" y="126" width="62" height="18" fill="#fff" stroke="#cfd8df"/><text x="71" y="139" text-anchor="middle">&#8776;83 kN</text>
    <rect x="249" y="126" width="62" height="18" fill="#fff" stroke="#cfd8df"/><text x="280" y="139" text-anchor="middle">&#8776;166 kN</text>
    <rect x="449" y="126" width="62" height="18" fill="#fff" stroke="#cfd8df"/><text x="480" y="139" text-anchor="middle">&#8776;83 kN</text>
    <rect x="38" y="262" width="66" height="18" fill="#fff" stroke="#cfd8df"/><text x="71" y="275" text-anchor="middle">&#8776;166 kN</text>
    <rect x="246" y="262" width="68" height="18" fill="#fff" stroke="#c0392b"/><text x="280" y="275" text-anchor="middle" font-weight="bold">&#8776;332 kN</text>
    <rect x="447" y="262" width="66" height="18" fill="#fff" stroke="#cfd8df"/><text x="480" y="275" text-anchor="middle">&#8776;166 kN</text>
  </g>
  <text x="280" y="392" text-anchor="middle" font-size="11.5" fill="#5a6b78">Column force comes from the load path: interior &#8776; 2&#215; corner; lower storey &#8776; 2&#215; upper.</text>
</svg>
<figcaption><b>Figure 7.</b> Why a global frame solve matters: the interior column collects from beams on
both sides (&#8776;2&#215; a corner column) and a lower column carries the floors above it (Chapter 8).</figcaption>
</figure>
"""

FIG_MATCH = """
<figure class="fig" style="max-width:540px">
<svg viewBox="0 0 560 420" role="img" aria-label="Bipartite matching of reclaimed members to new-design slots">
  <text x="140" y="28" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a2b3c">Reclaimed supply</text>
  <text x="420" y="28" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a2b3c">New-design slots</text>
  <!-- edges first (under the nodes) -->
  <g fill="none">
    <line x1="240" y1="78"  x2="320" y2="78"  stroke="#1e8449" stroke-width="3.5"/>
    <line x1="240" y1="143" x2="320" y2="143" stroke="#1e8449" stroke-width="3.5"/>
    <line x1="240" y1="208" x2="320" y2="208" stroke="#1e8449" stroke-width="3.5"/>
    <line x1="240" y1="143" x2="320" y2="78"  stroke="#8a97a3" stroke-width="1.4" stroke-dasharray="5 4"/>
  </g>
  <!-- supply nodes -->
  <g font-size="12" fill="#1a2b3c">
    <rect x="40" y="55"  width="200" height="46" rx="6" fill="#e8f0f5" stroke="#2c5f7c"/><text x="140" y="83"  text-anchor="middle">IPE300 &#183; 7.8 m</text>
    <rect x="40" y="120" width="200" height="46" rx="6" fill="#e8f0f5" stroke="#2c5f7c"/><text x="140" y="148" text-anchor="middle">IPE360 &#183; 9.0 m</text>
    <rect x="40" y="185" width="200" height="46" rx="6" fill="#e8f0f5" stroke="#2c5f7c"/><text x="140" y="213" text-anchor="middle">HEB240 &#183; 5.2 m</text>
    <rect x="40" y="250" width="200" height="46" rx="6" fill="#f3f3f3" stroke="#bbb" stroke-dasharray="5 4"/><text x="140" y="278" text-anchor="middle" fill="#8a97a3">IPE300 &#183; 4.0 m</text>
  </g>
  <!-- demand nodes -->
  <g font-size="11.5" fill="#1a2b3c">
    <rect x="320" y="55"  width="200" height="46" rx="6" fill="#eef3f7" stroke="#2c5f7c"/><text x="420" y="83"  text-anchor="middle">Beam &#183; &#8805;IPE270 &#183; 6 m</text>
    <rect x="320" y="120" width="200" height="46" rx="6" fill="#eef3f7" stroke="#2c5f7c"/><text x="420" y="148" text-anchor="middle">Beam &#183; &#8805;IPE330 &#183; 8.5 m</text>
    <rect x="320" y="185" width="200" height="46" rx="6" fill="#eef3f7" stroke="#2c5f7c"/><text x="420" y="213" text-anchor="middle">Column &#183; &#8805;HEB200 &#183; 4 m</text>
    <rect x="320" y="250" width="200" height="46" rx="6" fill="#fdecea" stroke="#c0392b" stroke-width="2"/><text x="420" y="278" text-anchor="middle">Beam &#183; &#8805;IPE400 &#183; 7 m</text>
  </g>
  <text x="535" y="278" text-anchor="end" font-size="10" fill="#c0392b"> </text>
  <text x="252" y="278" font-size="10.5" fill="#8a97a3">unused</text>
  <text x="528" y="318" text-anchor="end" font-size="10.5" fill="#c0392b">&#8593; needs new steel</text>
  <!-- legend -->
  <line x1="40" y1="360" x2="70" y2="360" stroke="#1e8449" stroke-width="3.5"/>
  <text x="76" y="364" font-size="11" fill="#5a6b78">chosen optimal match (max CO&#8322; saved)</text>
  <line x1="40" y1="384" x2="70" y2="384" stroke="#8a97a3" stroke-width="1.4" stroke-dasharray="5 4"/>
  <text x="76" y="388" font-size="11" fill="#5a6b78">other feasible (not chosen)</text>
  <rect x="330" y="352" width="16" height="14" fill="#fdecea" stroke="#c0392b" stroke-width="1.6"/>
  <text x="352" y="364" font-size="11" fill="#5a6b78">no reclaimed member fits &#8594; buy new</text>
</svg>
<figcaption><b>Figure 8.</b> The matching, as a picture: a reclaimed member may fill a slot only if it
passes that slot's checks (an edge). The optimiser picks the green set that saves the most carbon
(Chapter 10).</figcaption>
</figure>
"""

FIGURES = {
    "FIG-CARBON": FIG_CARBON, "FIG-SECTION": FIG_SECTION, "FIG-FAILURE": FIG_FAILURE,
    "FIG-PIPELINE": FIG_PIPELINE, "FIG-TRIB": FIG_TRIB, "FIG-BEAM": FIG_BEAM,
    "FIG-FRAME": FIG_FRAME, "FIG-MATCH": FIG_MATCH,
}

CSS = """
@page { size: A4; margin: 18mm 17mm 18mm 17mm; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; box-sizing: border-box; }
html { font-size: 10.7pt; }
body { font-family: "Cambria","Georgia",serif; color: #1a2b3c; line-height: 1.5; margin: 0; }
h1, h2, h3, h4 { font-family: "Segoe UI","Calibri",sans-serif; color: #14304a; line-height: 1.25; }
main h1 { font-size: 19pt; break-before: page; border-bottom: 2px solid #2c5f7c; padding-bottom: 4px; margin: 0 0 14px; }
h2 { font-size: 14pt; margin: 20px 0 7px; }
h3 { font-size: 12pt; margin: 15px 0 5px; color: #2c5f7c; }
p { margin: 7px 0; text-align: justify; }
ul, ol { margin: 7px 0; padding-left: 22px; }
li { margin: 3px 0; }
strong { color: #11253a; }
code { font-family: "Consolas","Cascadia Mono",monospace; font-size: 0.9em; background: #eef2f5; padding: 0 3px; border-radius: 3px; }
pre { background: #f4f7f9; border: 1px solid #d6dee4; border-left: 4px solid #2c5f7c; border-radius: 4px;
      padding: 8px 12px; overflow-x: auto; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 0.86em; line-height: 1.45; }
blockquote { margin: 10px 0; padding: 7px 14px; background: #f3f7fa; border-left: 4px solid #8aa9bd;
             color: #2b4257; break-inside: avoid; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 9.6pt; break-inside: avoid; }
th, td { border: 1px solid #c4d0d9; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #e8f0f5; font-family: "Segoe UI",sans-serif; }
tr:nth-child(even) td { background: #f7fafc; }
hr { border: none; border-top: 1px solid #d6dee4; margin: 16px 0; }
a { color: #1f6391; text-decoration: none; }
figure.fig { margin: 16px auto; text-align: center; break-inside: avoid; page-break-inside: avoid; }
figure.fig svg { width: 100%; height: auto; }
figure.fig svg text { font-family: "Segoe UI","Calibri",sans-serif; }
figcaption { font-size: 9.2pt; color: #5a6b78; margin-top: 6px; text-align: center;
             font-family: "Segoe UI",sans-serif; line-height: 1.4; }
/* title page */
.titlepage { text-align: center; height: 247mm; display: flex; flex-direction: column;
             justify-content: center; break-after: page; }
.titlepage h1 { font-size: 25pt; border: none; break-before: avoid; margin: 0 0 6px; }
.titlepage h3 { font-size: 13.5pt; color: #2c5f7c; font-weight: normal; margin: 0 0 26px; }
.titlepage p { text-align: center; margin: 3px 0; }
.titlepage blockquote { text-align: left; max-width: 150mm; margin: 26px auto 0; font-size: 9.4pt; }
.titlepage hr { display: none; }
"""


def build_html() -> str:
    raw = MD.read_text(encoding="utf-8")
    lines = raw.split("\n")
    hr_idx = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    split = hr_idx[1] if len(hr_idx) >= 2 else 0          # 2nd '---' ends the title block
    front_lines = [ln for ln in lines[:split] if ln.strip() != "---"]
    body_lines = lines[split + 1:]

    md_front = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    md_body = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    front_html = md_front.convert("\n".join(front_lines))
    body_html = md_body.convert("\n".join(body_lines))

    # Swap each [[FIG-*]] token (rendered by markdown as <p>[[FIG-*]]</p>) for its figure.
    for key, svg in FIGURES.items():
        body_html = re.sub(rf"<p>\s*\[\[{key}\]\]\s*</p>", svg.strip(), body_html)

    leftover = re.findall(r"\[\[FIG-[A-Z]+\]\]", body_html)
    if leftover:
        print("WARNING: unreplaced figure tokens:", leftover)

    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Circular Structural Reuse Matcher &mdash; Thesis</title>"
        f"<style>{CSS}</style></head><body>"
        f"<header class='titlepage'>{front_html}</header>"
        f"<main>{body_html}</main>"
        "</body></html>"
    )


def find_edge() -> str | None:
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if Path(p).exists():
            return p
    return None


def render_pdf() -> bool:
    edge = find_edge()
    if not edge:
        print("No Edge/Chrome found; THESIS.html is ready to print manually.")
        return False
    url = HTML.resolve().as_uri()
    with tempfile.TemporaryDirectory() as profile:
        cmd = [
            edge, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
            "--no-first-run", "--disable-extensions",
            f"--user-data-dir={profile}",
            "--virtual-time-budget=20000",
            f"--print-to-pdf={PDF}", url,
        ]
        print("Rendering PDF via:", Path(edge).name)
        try:
            subprocess.run(cmd, timeout=180, check=False, capture_output=True)
        except Exception as exc:  # noqa: BLE001
            print("Edge run error:", exc)
    return PDF.exists()


def main() -> int:
    html = build_html()
    HTML.write_text(html, encoding="utf-8")
    print(f"Wrote {HTML.name} ({len(html) // 1024} KB)")
    ok = render_pdf()
    if ok:
        print(f"Wrote {PDF.name} ({PDF.stat().st_size // 1024} KB)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
