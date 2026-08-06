%% paper-columns.ily
%%
%% Included via  lilypond -dinclude-settings=paper-columns.ily
%%
%% Makes LilyPond report, on stderr, where every musical moment ended up
%% horizontally.  One line per paper column:
%%
%%     @COL 0 1 3/16 37.164966867090826
%%          ^ ^  ^   ^
%%          | |  |   x of the column in LilyPond units (staff spaces), relative
%%          | |  |   to the left edge of the system it landed in
%%          | |  musical moment in whole notes, as an exact rational
%%          | 1 = musical column (a note/chord/rest onset)
%%          | 0 = non-musical column (breakable: barline, clef, line end)
%%          index of the containing system, in order of first appearance
%%
%% A paper column exists at every distinct moment at which anything is printed,
%% and after line breaking it knows its own position.  That is exactly the
%% (when, where) pair a score-following playhead needs, straight from the
%% engraver: no note-head image analysis, and no guessing which MIDI onset
%% belongs to which printed note.
%%
%% Nothing about the layout changes -- `after-line-breaking` is a read-only hook
%% that runs once positions are final -- so these coordinates describe exactly
%% the engraving produced by the same run, and this file can be combined with
%% analysis-colors.ily in a single compilation pass.
%%
%% Two things to know when consuming the output:
%%
%%   - A breakable column at a line break is reported TWICE: once at the right
%%     margin of the system ending there, once at x=0 of the system beginning
%%     there.  The system index disambiguates them.  (Doing it by x instead
%%     fails on a ragged last system, whose line end is nowhere near the
%%     right margin.)
%%   - Grace notes share their main note's moment (`ly:moment-main` discards the
%%     grace part), so one moment can carry two musical columns in one system.
%%     The larger x is the main note.

#(define lilymusic-systems '())

#(define (lilymusic-system-index sys)
   ;; Systems have no stable printed identity, so hand out indices by object
   ;; identity in order of first appearance.  That order is not guaranteed to
   ;; be reading order -- the consumer sorts systems by their earliest moment.
   (let ((known (assq sys lilymusic-systems)))
     (if known
         (cdr known)
         (let ((i (length lilymusic-systems)))
           (set! lilymusic-systems (cons (cons sys i) lilymusic-systems))
           i))))

#(define (lilymusic-dump-column musical)
   (lambda (grob)
     (let ((sys (ly:grob-system grob))
           (w   (ly:grob-property grob 'when)))
       (if (and (ly:grob? sys) (ly:moment? w))
           (format (current-error-port) "@COL ~a ~a ~a ~a~%"
                   (lilymusic-system-index sys)
                   musical
                   (ly:moment-main w)
                   (ly:grob-relative-coordinate grob sys X))))
     #t))

\layout {
  \context {
    \Score
    \override PaperColumn.after-line-breaking = #(lilymusic-dump-column 1)
    \override NonMusicalPaperColumn.after-line-breaking = #(lilymusic-dump-column 0)
  }
}
