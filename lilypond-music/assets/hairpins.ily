%% hairpins.ily
%%
%% Included via  lilypond -dinclude-settings=hairpins.ily
%%
%% Reports, on stderr, every staff in the score and every crescendo and
%% diminuendo in it:
%%
%%     @STAFF 3                staff 3 exists (emitted in creation order)
%%     @HP 7 3 13 14 -1        hairpin 7, staff 3, moments 13..14, diminuendo
%%
%% Why this is needed: a hairpin is performed in MIDI as a ramp of note
%% velocities, and velocity is fixed when a note begins.  A hairpin drawn across
%% a single held note therefore produces no sound at all -- `<d a>1\>` is
%% printed and silent.  Knowing where the hairpins are is what lets the pipeline
%% synthesise those swells as CC11 expression instead.
%%
%% Staves are numbered by *context creation order*, which is the order LilyPond
%% writes MIDI tracks in, so the index needs no further translation.  An
%% engraver in each staff picks up its own index and stamps it onto every
%% hairpin it sees, which is why no geometry is involved: attributing hairpins
%% by vertical position on the page would work, but it would depend on the
%% engraving rather than on the score's structure.
%%
%% A hairpin crossing a line break is engraved as two pieces with two spans;
%% both carry the same id, so the consumer merges them.  Adjacency alone would
%% wrongly merge a `\!` immediately followed by a new `\>`.
%%
%% Two constraints learned the hard way, both of which fail silently:
%%
%%   - Do NOT hook StaffSymbol.after-line-breaking to find staves.  It carries a
%%     C++ callback that is not reachable from Scheme, so an override replaces
%%     it rather than extending it, the staff lines are then drawn wrong, and
%%     system detection in lily_layout.py collapses -- with no error anywhere.
%%   - The `\Staff` block below must not set anything analysis-colors.ily also
%%     sets on `\Staff`, because a later \layout block displaces the earlier
%%     definition instead of merging with it.  `\consists` adds an engraver and
%%     touches no grob properties, so the two coexist.
%%
%% `after-line-breaking` is read-only, so nothing here changes the engraving.

#(define lilymusic-contexts '())
#(define lilymusic-hairpins '())
#(define lilymusic-hairpin-staff '())

#(define (lilymusic-index! key getter setter)
   (let ((known (assq key (getter))))
     (if known
         (cdr known)
         (let ((i (length (getter))))
           (setter (cons (cons key i) (getter)))
           i))))

#(define (lilymusic-context-index ctx)
   (lilymusic-index! ctx
                     (lambda () lilymusic-contexts)
                     (lambda (v) (set! lilymusic-contexts v))))

#(define (lilymusic-hairpin-id grob)
   (lilymusic-index! (ly:grob-original grob)
                     (lambda () lilymusic-hairpins)
                     (lambda (v) (set! lilymusic-hairpins v))))

#(define lilymusic-staff-watcher
   (make-engraver
     ((initialize translator)
      (format (current-error-port) "@STAFF ~a~%"
              (lilymusic-context-index (ly:translator-context translator))))
     (acknowledgers
       ((hairpin-interface engraver grob source-engraver)
        (set! lilymusic-hairpin-staff
              (cons (cons (ly:grob-original grob)
                          (lilymusic-context-index
                            (ly:translator-context engraver)))
                    lilymusic-hairpin-staff))))))

#(define (lilymusic-staff-of grob)
   (let ((known (assq (ly:grob-original grob) lilymusic-hairpin-staff)))
     (if known (cdr known) -1)))

#(define (lilymusic-dump-hairpin grob)
   (let ((left (ly:spanner-bound grob LEFT))
         (right (ly:spanner-bound grob RIGHT)))
     (if (and (ly:grob? left) (ly:grob? right))
         (format (current-error-port) "@HP ~a ~a ~a ~a ~a~%"
                 (lilymusic-hairpin-id grob)
                 (lilymusic-staff-of grob)
                 (ly:moment-main (ly:grob-property (ly:item-get-column left) 'when))
                 (ly:moment-main (ly:grob-property (ly:item-get-column right) 'when))
                 (ly:grob-property grob 'grow-direction))))
   ;; Hairpin ships a real after-line-breaking callback.  Overriding the
   ;; property replaces it, so call it here: dropping it leaves degenerate
   ;; zero-length hairpins in place, which can nudge the spacing of the
   ;; instrumented render away from the display render it is measured against.
   (ly:spanner::kill-zero-spanned-time grob))

\layout {
  \context {
    \Score
    \override Hairpin.after-line-breaking = #lilymusic-dump-hairpin
  }
  \context { \Staff         \consists #lilymusic-staff-watcher }
  \context { \DrumStaff     \consists #lilymusic-staff-watcher }
  \context { \RhythmicStaff \consists #lilymusic-staff-watcher }
  \context { \TabStaff      \consists #lilymusic-staff-watcher }
}
