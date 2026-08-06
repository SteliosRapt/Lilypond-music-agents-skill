%% analysis-colors.ily
%%
%% Included via  lilypond -dinclude-settings=analysis-colors.ily
%% Produces a *layout-identical* copy of the score in which:
%%   - every barline (and span bar) is pure red   -> machine-detectable bar positions
%%   - every staff line is pure blue              -> machine-detectable staff extents
%%   - every system-start delimiter is green      -> machine-detectable system bands
%%
%% Nothing about spacing changes, so pixel coordinates measured here are valid
%% for the normally-coloured render of the same score at the same resolution.

\layout {
  \context {
    \Score
    \override BarLine.color = #(rgb-color 1 0 0)
    \override SpanBar.color = #(rgb-color 1 0 0)
    %% the vertical line/brace/bracket at the left of each system: one per
    %% system, spanning exactly its staves -> the system delimiter
    \override SystemStartBar.color = #(rgb-color 0 0.6 0)
    \override SystemStartBrace.color = #(rgb-color 0 0.6 0)
    \override SystemStartBracket.color = #(rgb-color 0 0.6 0)
    \override SystemStartSquare.color = #(rgb-color 0 0.6 0)
  }
  \context { \Staff         \override StaffSymbol.color = #(rgb-color 0 0 1) }
  \context { \DrumStaff     \override StaffSymbol.color = #(rgb-color 0 0 1) }
  \context { \RhythmicStaff \override StaffSymbol.color = #(rgb-color 0 0 1) }
  \context { \TabStaff      \override StaffSymbol.color = #(rgb-color 0 0 1) }
  \context { \VaticanaStaff \override StaffSymbol.color = #(rgb-color 0 0 1) }
}
