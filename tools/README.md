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
