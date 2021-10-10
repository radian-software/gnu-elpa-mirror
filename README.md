# GNU ELPA Mirror

This repository hosts a tool for maintaining a GitHub mirror of the
[GNU ELPA][gnu-elpa] Emacs Lisp Package Archive. The service is
deployed to Heroku and runs daily.

## Why?

GNU ELPA uses an overly complex, unwieldy, and fragile build process.
Therefore, running GNU ELPA packages directly from source is not an
easy task. In addition to the added complexity when compared to
[MELPA], running some packages requires a local checkout of the Emacs
source repository.

These problems are all neatly avoided if the GNU ELPA build process is
run on Heroku and the results are mirrored to GitHub, so that the
packages can be easily run from source by anyone.

Now, after I set this up, I found that it could be used to solve other
problems as well. One is that cloning [epkgs], which `straight.el`
uses as an index for the Emacsmirror, takes a while, because it
includes a full SQL database. Since `straight.el` doesn't actually
need the contents of that SQL database, only the other information in
the repository, GNU ELPA Mirror makes available [a
mirror][emacsmirror-mirror] of this repository containing only the
small files that `straight.el` actually needs. Note that the actual
Emacsmirror packages are not mirrored---that would be somewhat
absurd---only the index.

## Usage

There is a [listing of all mirrored GNU ELPA
packages][mirror-package-list]. This should correlate roughly with the
[list of published GNU ELPA packages][gnu-elpa-package-list].
Furthermore there is a [limited mirror of the Emacsmirror
index][emacsmirror-mirror], which corresponds to information available
in [epkgs].

The package named `<foo>` can be found at the URL:

    https://github.com/emacs-straight/<foo>

If you use the package manager [`straight.el`][straight.el], these
packages will be automatically used (provided they are not also
available from [MELPA] or [Org ELPA][org-elpa]) with the following
configuration, enabled by default:

    (setq straight-recipes-gnu-elpa-use-mirror t)
    (setq straight-recipes-emacsmirror-use-mirror t)

This is also documented in the `straight.el` documentation.

## Deployment

* Create a Heroku app named `gnu-elpa-mirror`.
* Add the following buildpacks:

      https://github.com/moneymeets/python-poetry-buildpack.git
      heroku/python
      https://github.com/raxod502/heroku-buildpack-emacs

* Set the `ACCESS_TOKEN` config var to a GitHub personal access token
  with the `public_repo` permission.
* Set the `DISABLE_POETRY_CREATE_RUNTIME_FILE` config var to `1`.
* Add the [Heroku Scheduler][scheduler] addon.
* Configure Scheduler to run the command `./cron.daily.sh` every day.
* Set up automatic deploys when pushing to GitHub.

### Debugging

Run it locally (in a virtualenv, after installing from
`requirements.txt`):

    $ python3 -m gnu_elpa_mirror

Test the cron job manually:

    $ heroku run ./cron.daily.sh

Interactive testing:

    $ heroku run bash

[epkgs]: https://github.com/emacsmirror/epkgs
[emacsmirror-mirror]: https://github.com/emacs-straight/emacsmirror-mirror
[gnu-elpa]: https://elpa.gnu.org/
[gnu-elpa-package-list]: https://elpa.gnu.org/packages/
[melpa]: https://melpa.org/#/
[mirror-package-list]: https://github.com/emacs-straight/gnu-elpa-mirror
[org-elpa]: https://orgmode.org/elpa.html
[scheduler]: https://elements.heroku.com/addons/scheduler
[straight.el]: https://github.com/raxod502/straight.el
