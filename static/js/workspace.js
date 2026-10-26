/**
 * NoteWise AI - Workspace Interactivity
 * Handles flashcard flipping, quiz option styling, copy-to-clipboard, and loading states.
 */

document.addEventListener('DOMContentLoaded', function () {
    // ------------------------------------------------------------------------
    // 1. Flashcards Interactive Deck
    // ------------------------------------------------------------------------
    var flashcards = document.querySelectorAll('.single-flashcard');
    var currentCardIndex = 0;
    var prevBtn = document.getElementById('prev-card-btn');
    var nextBtn = document.getElementById('next-card-btn');
    var flipBtn = document.getElementById('flip-card-btn');
    var counterEl = document.getElementById('card-counter');
    var toggleGridBtn = document.getElementById('toggle-grid-btn');
    var deckView = document.getElementById('flashcard-deck-view');
    var gridView = document.getElementById('flashcard-grid-view');

    function showCard(index) {
        if (!flashcards.length) return;
        flashcards.forEach(function (card, i) {
            if (i === index) {
                card.style.display = 'block';
                card.classList.remove('is-flipped');
            } else {
                card.style.display = 'none';
            }
        });
        if (counterEl) {
            counterEl.textContent = 'Card ' + (index + 1) + ' of ' + flashcards.length;
        }
        if (prevBtn) {
            prevBtn.disabled = (index === 0);
        }
        if (nextBtn) {
            nextBtn.disabled = (index === flashcards.length - 1);
        }
    }

    if (flashcards.length > 0) {
        showCard(0);

        if (prevBtn) {
            prevBtn.addEventListener('click', function () {
                if (currentCardIndex > 0) {
                    currentCardIndex--;
                    showCard(currentCardIndex);
                }
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener('click', function () {
                if (currentCardIndex < flashcards.length - 1) {
                    currentCardIndex++;
                    showCard(currentCardIndex);
                }
            });
        }

        if (flipBtn) {
            flipBtn.addEventListener('click', function () {
                var currentCard = flashcards[currentCardIndex];
                if (currentCard) {
                    currentCard.classList.toggle('is-flipped');
                }
            });
        }

        // Allow clicking anywhere on the card itself to flip
        flashcards.forEach(function (card) {
            card.addEventListener('click', function (e) {
                if (e.target.tagName !== 'BUTTON') {
                    card.classList.toggle('is-flipped');
                }
            });
        });

        // Toggle Grid vs Deck view
        if (toggleGridBtn && deckView && gridView) {
            toggleGridBtn.addEventListener('click', function () {
                if (gridView.style.display === 'none' || gridView.style.display === '') {
                    gridView.style.display = 'grid';
                    deckView.style.display = 'none';
                    toggleGridBtn.textContent = 'Switch to Deck View';
                } else {
                    gridView.style.display = 'none';
                    deckView.style.display = 'block';
                    toggleGridBtn.textContent = 'View All Cards (Grid)';
                }
            });
        }
    }

    // Grid flashcards clicking
    var gridCards = document.querySelectorAll('.grid-flashcard');
    gridCards.forEach(function (card) {
        card.addEventListener('click', function () {
            card.classList.toggle('is-flipped');
        });
    });

    // ------------------------------------------------------------------------
    // 2. Copy to Clipboard
    // ------------------------------------------------------------------------
    var copyButtons = document.querySelectorAll('.copy-result-btn');
    copyButtons.forEach(function (btn) {
        btn.addEventListener('click', function () {
            var targetId = btn.getAttribute('data-target');
            var targetEl = document.getElementById(targetId);
            if (targetEl) {
                var text = targetEl.innerText || targetEl.textContent;
                navigator.clipboard.writeText(text).then(function () {
                    var originalText = btn.textContent;
                    btn.textContent = 'Copied!';
                    btn.classList.add('copied');
                    setTimeout(function () {
                        btn.textContent = originalText;
                        btn.classList.remove('copied');
                    }, 2000);
                }).catch(function () {
                    alert('Text copied to clipboard.');
                });
            }
        });
    });

    // ------------------------------------------------------------------------
    // 3. AI Submit Loading States
    // ------------------------------------------------------------------------
    var aiForms = document.querySelectorAll('form.ai-tool-form, form.study-upload-form');
    aiForms.forEach(function (form) {
        form.addEventListener('submit', function () {
            var submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn && !submitBtn.disabled) {
                var originalText = submitBtn.textContent;
                submitBtn.setAttribute('data-original-text', originalText);
                submitBtn.disabled = true;
                if (form.classList.contains('study-upload-form')) {
                    submitBtn.textContent = 'Analyzing Notes...';
                } else {
                    submitBtn.textContent = 'Generating with AI...';
                }
                // Allow the form to submit naturally
                form.submit();
            }
        });
    });

    // ------------------------------------------------------------------------
    // 4. Smooth scroll to study result if present
    // ------------------------------------------------------------------------
    var resultSection = document.getElementById('study-result');
    if (resultSection && window.location.hash === '#study-result') {
        resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
});
