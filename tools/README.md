# tools

Scripts that are not part of the application.

They are kept in the repository because somebody will need to run them again,
and they are kept *out* of `apps/` because the application should not grow a
PDF-generation dependency for the sake of one form.

    pip install reportlab
    python manage.py gaps --json data/_gaps.json
    python tools/chef_form.py data/_gaps.json "Woodlands-questions-for-the-kitchen.pdf"

## chef_form.py

Builds the questions-for-the-kitchen sheet as a fillable PDF: every blank is a
real form field, so the kitchen can type into it in Preview, Acrobat or a
browser and send it back, rather than printing it, writing on it and
photographing it.

The content comes from `manage.py gaps`, so the sheet shrinks by itself as
answers are recorded. Re-run both and the questions already answered are gone.

## count_list_form.py

Builds "What we count, and how often" — the sheet to take to the restaurant and
fill in by hand with the owners and the chef. Every item the system knows,
grouped by layer (made in the kitchen, groceries), with tick-box columns for
how often it is made or bought and how often it is counted; how each grocery
is bought is printed in where the system knows it, to be confirmed. Blank
pages for vegetables and packaging, which the system has none of yet.

Generated from the live item list and printed to PDF by Chrome, so it needs no
PDF library:

    PYTHONPATH=. python tools/count_list_form.py "Claude outputs/Woodlands-what-we-count.pdf"
