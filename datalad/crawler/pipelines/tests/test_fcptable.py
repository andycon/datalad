# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##


from os.path import exists
from ....utils import chpwd
from ....tests.utils import assert_true, assert_raises
from ....tests.utils import with_tempfile
from datalad.crawler.pipelines.tests.utils import _test_smoke_pipelines
from datalad.crawler.pipelines.fcptable import *
from datalad.crawler.pipeline import run_pipeline

import logging
from logging import getLogger
lgr = getLogger('datalad.crawl.tests')

from ..fcptable import pipeline, superdataset_pipeline


def test_smoke_pipelines():
    yield _test_smoke_pipelines, pipeline, 'bogus'
    yield _test_smoke_pipelines, superdataset_pipeline, None


@with_tempfile(mkdir=True)
def _test_dataset(dataset, error, create, tmpdir):
    TOPURL = "http://fcon_1000.projects.nitrc.org/fcpClassic/FcpTable.html"

    with chpwd(tmpdir):

        if create:
            with open("README.txt", 'w') as f:
                f.write(" ")

        pipe = [
            crawl_url(TOPURL),
            [
                assign({'dataset': dataset}),
                sub({'response': {'<div class="tableParam">([^<]*)</div>': r'\1'}}),
                find_dataset(dataset),
                extract_readme,
            ]
        ]

        if error:
            assert_raises(RuntimeError, run_pipeline, pipe)
            return

        run_pipeline(pipe)
        assert_true(exists("README.txt"))

        f = open("README.txt", 'r')
        contents = f.read()
        assert_true("Author(s)" and "Details" in contents)


def test_dataset():
    yield _test_dataset, 'Baltimore', None, False
    yield _test_dataset, 'AnnArbor_b', None, False
    yield _test_dataset, 'Ontario', None, False
    yield _test_dataset, 'Boston', RuntimeError, False
    yield _test_dataset, "AnnArbor_b", None, True

