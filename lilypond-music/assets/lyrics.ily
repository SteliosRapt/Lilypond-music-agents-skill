%% lyrics.ily
%%
%% Included via  lilypond -dinclude-settings=lyrics.ily
%%
%% Reports, on stderr, everything a singing-voice synthesiser needs about the
%% vocal parts of a score -- which notes carry which syllable, and which notes
%% are a continuation of the syllable before them:
%%
%%     @STAFF 0                staff 0 exists (context creation order)
%%     @VOICE 0 singer         voice "singer" lives in staff 0
%%     @META 0 4/4 63          staff 0: metre and tempo in quarters per minute
%%     @TEMPO 0.0 63           tempo change: moment, quarters per minute
%%     @KEY  0 -2              staff 0: key signature, sharps (negative = flats)
%%     @NOTE singer 6.5 .25 74 a note: voice, moment, duration, MIDI pitch
%%     @TIE  singer 7.0        the note at that moment is tied to the next
%%     @LYR  0 singer          lyric line 0 is sung by voice "singer"
%%     @SYL  0 6.5 "Lan"       lyric line 0, moment, syllable
%%     @HYPH 0 6.5             that syllable is joined to the next by `--`
%%     @EXT  0 7.0             that syllable is extended by `__`
%%
%% Moments are in whole notes from the start of the score, matching the units
%% `midi_timing.py` already works in; the staff index is the same
%% context-creation index `hairpins.ily` emits, so it maps onto MIDI tracks
%% without translation.
%%
%% Why this exists.  Feeding a score to a singing synthesiser means answering
%% "which syllable is sung on which note", and that question is genuinely hard
%% to answer from the .ly source: `\lyricsto` skips notes under slurs and ties,
%% `--` splits a word across notes, `__` and `_` extend a syllable, and rests
%% and `\skip` shift everything after them.  LilyPond already resolves all of
%% that -- it has to, in order to place the syllables on the page.  Asking it
%% for the answer it computed is exact; re-deriving the alignment by pairing up
%% the note list and the lyric list in Python is guesswork that fails on the
%% first melisma.
%%
%% Melismata therefore need no special handling here.  A note with no syllable
%% at its moment IS the melisma: it continues the syllable that came before it.
%% Ties are reported separately because a tied pair is one sung note, not a
%% note plus a melisma, and the distinction matters to a synthesiser that
%% inserts a fresh consonant attack at every note onset.
%%
%% Note the empty syllable.  `_` in \lyricmode -- a note deliberately passed
%% over -- arrives here as a syllable whose text is a single space, not as an
%% absent event.  Consumers must drop it, or the singer sings a space.
%%
%% This file only listens.  It sets no grob property and adds no engraver that
%% draws anything, so an instrumented run engraves identically to a plain one
%% and its page images stay measurable by lily_layout.py.

#(define lilymusic-staves '())
#(define lilymusic-lyric-lines '())

#(define (lilymusic-idx! key getter setter)
   (let ((known (assq key (getter))))
     (if known
         (cdr known)
         (let ((i (length (getter))))
           (setter (cons (cons key i) (getter)))
           i))))

#(define (lilymusic-staff-idx ctx)
   (lilymusic-idx! ctx
                   (lambda () lilymusic-staves)
                   (lambda (v) (set! lilymusic-staves v))))

#(define (lilymusic-lyric-idx ctx)
   (lilymusic-idx! ctx
                   (lambda () lilymusic-lyric-lines)
                   (lambda (v) (set! lilymusic-lyric-lines v))))

#(define (lilymusic-now ctx)
   (exact->inexact (ly:moment-main (ly:context-current-moment ctx))))

%% Tempo lives in Score, not Staff: read it back rather than tracking it.
#(define (lilymusic-tempo ctx)
   (let ((wpm (ly:context-property (ly:context-find ctx 'Score) 'tempoWholesPerMinute #f)))
     (if (ly:moment? wpm)
         (exact->inexact (* 4 (ly:moment-main wpm)))
         0)))

%% A key signature reaches Scheme as an alist of (degree . alteration); the
%% number of fifths MusicXML wants is just the signed count of altered degrees.
#(define (lilymusic-fifths alist)
   (let ((n 0))
     (for-each (lambda (e)
                 (let ((a (cdr e)))
                   (cond ((> a 0) (set! n (+ n 1)))
                         ((< a 0) (set! n (- n 1))))))
               alist)
     n))

%% Metre, key and tempo, reported per staff as they are established.  A score
%% that changes any of them mid-piece emits a further @META for that staff.
#(define lilymusic-score-watcher
   (make-engraver
     ((initialize translator)
      (format (current-error-port) "@STAFF ~a~%"
              (lilymusic-staff-idx (ly:translator-context translator))))
     (listeners
       ((time-signature-event engraver event)
        (let* ((ctx (ly:translator-context engraver))
               (frac (ly:context-property ctx 'timeSignatureFraction '(4 . 4))))
          (format (current-error-port) "@META ~a ~a/~a ~a~%"
                  (lilymusic-staff-idx ctx)
                  (if (pair? frac) (car frac) 4)
                  (if (pair? frac) (cdr frac) 4)
                  (lilymusic-tempo ctx))))
       ((key-change-event engraver event)
        (let ((ctx (ly:translator-context engraver)))
          (format (current-error-port) "@KEY ~a ~a~%"
                  (lilymusic-staff-idx ctx)
                  (lilymusic-fifths (ly:event-property event 'pitch-alist '()))))))))

%% Tempo marks are broadcast to Score, not to Staff, so they need their own
%% listener.  `\tempo 4 = 63` arrives as a count plus the duration it counts.
#(define lilymusic-tempo-watcher
   (make-engraver
     (listeners
       ((tempo-change-event engraver event)
        (let* ((ctx (ly:translator-context engraver))
               (count (ly:event-property event 'metronome-count 0))
               (unit (ly:event-property event 'tempo-unit #f))
               (whole (if (ly:duration? unit)
                          (ly:moment-main (ly:duration-length unit))
                          1/4)))
          (format (current-error-port) "@TEMPO ~a ~a~%"
                  (lilymusic-now ctx)
                  (exact->inexact (* count whole 4))))))))

#(define lilymusic-note-watcher
   (make-engraver
     ((initialize translator)
      (let* ((ctx (ly:translator-context translator))
             (staff (ly:context-find ctx 'Staff)))
        (format (current-error-port) "@VOICE ~a ~s~%"
                (if (ly:context? staff) (lilymusic-staff-idx staff) -1)
                (ly:context-id ctx))))
     (listeners
       ((note-event engraver event)
        (let ((ctx (ly:translator-context engraver))
              (p (ly:event-property event 'pitch))
              (d (ly:event-property event 'duration)))
          (if (and (ly:pitch? p) (ly:duration? d))
              (format (current-error-port) "@NOTE ~s ~a ~a ~a~%"
                      (ly:context-id ctx)
                      (lilymusic-now ctx)
                      (exact->inexact (ly:moment-main (ly:duration-length d)))
                      (+ 60 (ly:pitch-semitones p))))))
       ((tie-event engraver event)
        (let ((ctx (ly:translator-context engraver)))
          (format (current-error-port) "@TIE ~s ~a~%"
                  (ly:context-id ctx) (lilymusic-now ctx)))))))

%% `associatedVoiceContext` is set by \lyricsto, and by \addlyrics via the
%% implicit voice it creates, but not until the first syllable is due -- which
%% is why the line is announced from the listener rather than at initialize.
#(define lilymusic-lyric-watcher
   (make-engraver
     (listeners
       ((lyric-event engraver event)
        (let* ((ctx (ly:translator-context engraver))
               (i (lilymusic-lyric-idx ctx))
               (av (ly:context-property ctx 'associatedVoiceContext #f)))
          (format (current-error-port) "@LYR ~a ~s~%" i
                  (if (ly:context? av)
                      (ly:context-id av)
                      (ly:context-property ctx 'associatedVoice "")))
          (format (current-error-port) "@SYL ~a ~a ~s~%"
                  i (lilymusic-now ctx) (ly:event-property event 'text))))
       ((hyphen-event engraver event)
        (let ((ctx (ly:translator-context engraver)))
          (format (current-error-port) "@HYPH ~a ~a~%"
                  (lilymusic-lyric-idx ctx) (lilymusic-now ctx))))
       ((extender-event engraver event)
        (let ((ctx (ly:translator-context engraver)))
          (format (current-error-port) "@EXT ~a ~a~%"
                  (lilymusic-lyric-idx ctx) (lilymusic-now ctx)))))))

\layout {
  \context { \Score         \consists #lilymusic-tempo-watcher }
  \context { \Staff         \consists #lilymusic-score-watcher }
  \context { \DrumStaff     \consists #lilymusic-score-watcher }
  \context { \RhythmicStaff \consists #lilymusic-score-watcher }
  \context { \TabStaff      \consists #lilymusic-score-watcher }
  \context { \Voice         \consists #lilymusic-note-watcher }
  \context { \Lyrics        \consists #lilymusic-lyric-watcher }
}
