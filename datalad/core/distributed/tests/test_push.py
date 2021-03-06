
# -*- coding: utf-8 -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""Test push

"""

from datalad.distribution.dataset import Dataset
from datalad.support.exceptions import (
    IncompleteResultsError,
    InsufficientArgumentsError,
)
from datalad.tests.utils import (
    assert_in,
    assert_in_results,
    assert_not_in,
    assert_not_in_results,
    assert_raises,
    assert_repo_status,
    assert_result_count,
    assert_status,
    eq_,
    neq_,
    ok_,
    ok_file_has_content,
    serve_path_via_http,
    skip_if_on_windows,
    skip_ssh,
    with_tempfile,
    with_tree,
    SkipTest,
)
from datalad.utils import (
    chpwd,
    Path,
)
from datalad.support.gitrepo import GitRepo
from datalad.support.annexrepo import AnnexRepo
from datalad.core.distributed.clone import Clone
from datalad.core.distributed.push import Push
from datalad.support.network import get_local_file_url


@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_invalid_call(origin, tdir):
    ds = Dataset(origin).create()
    # no target
    assert_status('impossible', ds.push(on_failure='ignore'))
    # no dataset
    with chpwd(tdir):
        assert_raises(InsufficientArgumentsError, Push.__call__)
    # dataset, but outside path
    assert_raises(IncompleteResultsError, ds.push, path=tdir)
    # given a path constraint that doesn't match anything, will cause
    # nothing to be done
    assert_status('notneeded', ds.push(path=ds.pathobj / 'nothere'))

    # unavailable subdataset
    dummy_sub = ds.create('sub')
    dummy_sub.uninstall()
    assert_in('sub', ds.subdatasets(fulfilled=False, result_xfm='relpaths'))
    # now an explicit call to publish the unavailable subdataset
    assert_raises(ValueError, ds.push, 'sub')

    target = mk_push_target(ds, 'target', tdir, annex=True)
    # revision that doesn't exist
    assert_raises(
        ValueError,
        ds.push, to='target', since='09320957509720437523')


def mk_push_target(ds, name, path, annex=True, bare=True):
    # life could be simple, but nothing is simple on windows
    #src.create_sibling(dst_path, name='target')
    if annex:
        if bare:
            target = GitRepo(path=path, bare=True, create=True)
            target.call_git(['annex', 'init'])
        else:
            target = AnnexRepo(path, init=True, create=True)
            if not target.is_managed_branch():
                # for managed branches we need more fireworks->below
                target.config.set(
                    'receive.denyCurrentBranch', 'updateInstead',
                    where='local')
    else:
        target = GitRepo(path=path, bare=bare, create=True)
    ds.siblings('add', name=name, url=path, result_renderer=None)
    if annex and not bare and target.is_managed_branch():
        # maximum complication
        # the target repo already has a commit that is unrelated
        # to the source repo, because it has built a reference
        # commit for the managed branch.
        # the only sane approach is to let git-annex establish a shared
        # history
        ds.repo.call_git(['annex', 'sync'])
        ds.repo.call_git(['annex', 'sync', '--cleanup'])
    return target


@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def check_push(annex, src_path, dst_path):
    # prepare src
    src = Dataset(src_path).create(annex=annex)
    src_repo = src.repo
    # push should not add branches to the local dataset
    orig_branches = src_repo.get_branches()
    assert_not_in('synced/master', orig_branches)

    res = src.push(on_failure='ignore')
    assert_result_count(res, 1)
    assert_in_results(
        res, status='impossible',
        message='No push target given, and none could be auto-detected, '
        'please specific via --to')
    eq_(orig_branches, src_repo.get_branches())
    # target sibling
    target = mk_push_target(src, 'target', dst_path, annex=annex)
    eq_(orig_branches, src_repo.get_branches())

    res = src.push(to="target")
    eq_(orig_branches, src_repo.get_branches())
    assert_result_count(res, 2 if annex else 1)
    assert_in_results(
        res,
        action='publish', status='ok', target='target',
        refspec='refs/heads/master:refs/heads/master',
        operations=['new-branch'])

    assert_repo_status(src_repo, annex=annex)
    eq_(list(target.get_branch_commits_("master")),
        list(src_repo.get_branch_commits_("master")))

    # configure a default merge/upstream target
    src.config.set('branch.master.remote', 'target', where='local')
    src.config.set('branch.master.merge', 'master', where='local')

    # don't fail when doing it again, no explicit target specification
    # needed anymore
    res = src.push()
    eq_(orig_branches, src_repo.get_branches())
    # and nothing is pushed
    assert_status('notneeded', res)

    assert_repo_status(src_repo, annex=annex)
    eq_(list(target.get_branch_commits_("master")),
        list(src_repo.get_branch_commits_("master")))

    # some modification:
    (src.pathobj / 'test_mod_file').write_text("Some additional stuff.")
    src.save(to_git=True, message="Modified.")
    (src.pathobj / 'test_mod_annex_file').write_text("Heavy stuff.")
    src.save(to_git=not annex, message="Modified again.")
    assert_repo_status(src_repo, annex=annex)

    # we could say since='HEAD~2' to make things fast, or we are lazy
    # and say since='^' to indicate the state of the tracking remote
    # which is the same, because we made to commits since the last push.
    res = src.push(to='target', since="^", jobs=2)
    assert_in_results(
        res,
        action='publish', status='ok', target='target',
        refspec='refs/heads/master:refs/heads/master',
        # we get to see what happened
        operations=['fast-forward'])
    if annex:
        # we got to see the copy result for the annexed files
        assert_in_results(
            res,
            action='copy',
            status='ok',
            path=str(src.pathobj / 'test_mod_annex_file'))
        # we published, so we can drop and reobtain
        ok_(src_repo.file_has_content('test_mod_annex_file'))
        src_repo.drop('test_mod_annex_file')
        ok_(not src_repo.file_has_content('test_mod_annex_file'))
        src_repo.get('test_mod_annex_file')
        ok_(src_repo.file_has_content('test_mod_annex_file'))
        ok_file_has_content(
            src_repo.pathobj / 'test_mod_annex_file',
            'Heavy stuff.')

    eq_(list(target.get_branch_commits_("master")),
        list(src_repo.get_branch_commits_("master")))
    if not (annex and src_repo.is_managed_branch()):
        # the following doesn't make sense in managed branches, because
        # a commit that could be amended is no longer the last commit
        # of a branch after a sync has happened (which did happen
        # during the last push above

        # amend and change commit msg in order to test for force push:
        src_repo.commit("amended", options=['--amend'])
        # push should be rejected (non-fast-forward):
        res = src.push(to='target', since='HEAD~2', on_failure='ignore')
        # fails before even touching the annex branch
        assert_result_count(res, 1)
        assert_in_results(
            res,
            action='publish', status='error', target='target',
            refspec='refs/heads/master:refs/heads/master',
            operations=['rejected', 'error'])
        # push with force=True works:
        res = src.push(to='target', since='HEAD~2', force='gitpush')
        assert_in_results(
            res,
            action='publish', status='ok', target='target',
            refspec='refs/heads/master:refs/heads/master',
            operations=['forced-update'])
        eq_(list(target.get_branch_commits_("master")),
            list(src_repo.get_branch_commits_("master")))

    # we do not have more branches than we had in the beginning
    # in particular no 'synced/master'
    eq_(orig_branches, src_repo.get_branches())


def test_push():
    yield check_push, False
    yield check_push, True


@with_tempfile
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_push_recursive(
        origin_path, src_path, dst_top, dst_sub, dst_subnoannex, dst_subsub):
    # dataset with two submodules and one subsubmodule
    origin = Dataset(origin_path).create()
    origin_subm1 = origin.create('sub m')
    origin_subm1.create('subsub m')
    origin.create('subm noannex', annex=False)
    origin.save()
    assert_repo_status(origin.path)
    # prepare src as a fresh clone with all subdatasets checkout out recursively
    # running on a clone should make the test scenario more different than
    # test_push(), even for the pieces that should be identical
    top = Clone.__call__(source=origin.path, path=src_path)
    sub, subsub, subnoannex = top.get(
        '.', recursive=True, get_data=False, result_xfm='datasets')

    target_top = mk_push_target(top, 'target', dst_top, annex=True)
    # subdatasets have no remote yet, so recursive publishing should fail:
    res = top.push(to="target", recursive=True, on_failure='ignore')
    assert_in_results(
        res, path=top.path, type='dataset',
        refspec='refs/heads/master:refs/heads/master',
        operations=['new-branch'], action='publish', status='ok',
        target='target')
    for d in (sub, subsub, subnoannex):
        assert_in_results(
            res, status='error', type='dataset', path=d.path,
            message=("Unknown target sibling '%s'.",
                     'target'))
    # now fix that and set up targets for the submodules
    target_sub = mk_push_target(sub, 'target', dst_sub, annex=True)
    target_subnoannex = mk_push_target(
        subnoannex, 'target', dst_subnoannex, annex=False)
    target_subsub = mk_push_target(subsub, 'target', dst_subsub, annex=True)

    # and same push call as above
    res = top.push(to="target", recursive=True)
    # topds skipped
    assert_in_results(
        res, path=top.path, type='dataset',
        action='publish', status='notneeded', target='target')
    # the rest pushed
    for d in (sub, subsub, subnoannex):
        assert_in_results(
            res, status='ok', type='dataset', path=d.path,
            refspec='refs/heads/master:refs/heads/master')
    # all correspondig branches match across all datasets
    for s, d in zip((top, sub, subnoannex, subsub),
                    (target_top, target_sub, target_subnoannex,
                     target_subsub)):
        eq_(list(s.repo.get_branch_commits_("master")),
            list(d.get_branch_commits_("master")))
        if s != subnoannex:
            eq_(list(s.repo.get_branch_commits_("git-annex")),
                list(d.get_branch_commits_("git-annex")))

    # rerun should not result in further pushes of master
    res = top.push(to="target", recursive=True)
    assert_not_in_results(
        res, status='ok', refspec="refs/heads/master:refs/heads/master")
    assert_in_results(
        res, status='notneeded', refspec="refs/heads/master:refs/heads/master")

    if top.repo.is_managed_branch():
        raise SkipTest(
            'Save/status of subdataset with managed branches is an still '
            'unresolved issue')

    # now annex a file in subsub
    test_copy_file = subsub.pathobj / 'test_mod_annex_file'
    test_copy_file.write_text("Heavy stuff.")
    # save all the way up
    assert_status(
        ('ok', 'notneeded'),
        top.save(message='subsub got something', recursive=True))
    assert_repo_status(top.path)
    # publish straight up, should be smart by default
    res = top.push(to="target", recursive=True)
    # we see 3 out of 4 datasets pushed (sub noannex was left unchanged)
    for d in (top, sub, subsub):
        assert_in_results(
            res, status='ok', type='dataset', path=d.path,
            refspec='refs/heads/master:refs/heads/master')
    # file content copied too
    assert_in_results(
        res,
        action='copy',
        status='ok',
        path=str(test_copy_file))
    # verify it is accessible, drop and bring back
    assert_status('ok', top.drop(str(test_copy_file)))
    ok_(not subsub.repo.file_has_content('test_mod_annex_file'))
    top.get(test_copy_file)
    ok_file_has_content(test_copy_file, 'Heavy stuff.')

    # make two modification
    (sub.pathobj / 'test_mod_annex_file').write_text('annex')
    (subnoannex.pathobj / 'test_mod_file').write_text('git')
    # save separately
    top.save(sub.pathobj, message='annexadd', recursive=True)
    top.save(subnoannex.pathobj, message='gitadd', recursive=True)
    # now only publish the latter one
    res = top.push(to="target", since='HEAD~1', recursive=True)
    # nothing copied, no reports on the other modification
    assert_not_in_results(res, action='copy')
    assert_not_in_results(res, path=sub.path)
    for d in (top, subnoannex):
        assert_in_results(
            res, status='ok', type='dataset', path=d.path,
            refspec='refs/heads/master:refs/heads/master')
    # an unconditional push should now pick up the remaining changes
    res = top.push(to="target", recursive=True)
    assert_in_results(
        res,
        action='copy',
        status='ok',
        path=str(sub.pathobj / 'test_mod_annex_file'))
    assert_in_results(
        res, status='ok', type='dataset', path=sub.path,
        refspec='refs/heads/master:refs/heads/master')
    for d in (top, subnoannex, subsub):
        assert_in_results(
            res, status='notneeded', type='dataset', path=d.path,
            refspec='refs/heads/master:refs/heads/master')


@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_push_subds_no_recursion(src_path, dst_top, dst_sub, dst_subsub):
    # dataset with one submodule and one subsubmodule
    top = Dataset(src_path).create()
    sub = top.create('sub m')
    test_file = sub.pathobj / 'subdir' / 'test_file'
    test_file.parent.mkdir()
    test_file.write_text('some')
    subsub = sub.create(sub.pathobj / 'subdir' / 'subsub m')
    top.save(recursive=True)
    assert_repo_status(top.path)
    target_top = mk_push_target(top, 'target', dst_top, annex=True)
    target_sub = mk_push_target(sub, 'target', dst_sub, annex=True)
    target_subsub = mk_push_target(subsub, 'target', dst_subsub, annex=True)
    # now publish, but NO recursion, instead give the parent dir of
    # both a subdataset and a file in the middle subdataset
    res = top.push(
        to='target',
        # give relative to top dataset to elevate the difficulty a little
        path=str(test_file.relative_to(top.pathobj).parent))
    assert_status('ok', res)
    assert_in_results(res, action='publish', type='dataset', path=top.path)
    assert_in_results(res, action='publish', type='dataset', path=sub.path)
    assert_in_results(res, action='copy', type='file', path=str(test_file))
    # the lowest-level subdataset isn't touched
    assert_not_in_results(
        res, action='publish', type='dataset', path=subsub.path)


@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_force_datatransfer(srcpath, dstpath):
    src = Dataset(srcpath).create()
    target = mk_push_target(src, 'target', dstpath, annex=True, bare=True)
    (src.pathobj / 'test_mod_annex_file').write_text("Heavy stuff.")
    src.save(to_git=False, message="New annex file")
    assert_repo_status(src.path, annex=True)
    whereis_prior = src.repo.whereis(files=['test_mod_annex_file'])[0]

    res = src.push(to='target', force='no-datatransfer')
    # nothing reported to be copied
    assert_not_in_results(res, action='copy')
    # we got the git-push nevertheless
    eq_(src.repo.get_hexsha('master'), target.get_hexsha('master'))
    # nothing moved
    eq_(whereis_prior, src.repo.whereis(files=['test_mod_annex_file'])[0])

    # now a push without forced no-transfer
    # we do not give since, so the non-transfered file is picked up
    # and transferred
    res = src.push(to='target', force=None)
    # no branch change, done before
    assert_in_results(res, action='publish', status='notneeded',
                      refspec='refs/heads/master:refs/heads/master')
    # but availability update
    assert_in_results(res, action='publish', status='ok',
                      refspec='refs/heads/git-annex:refs/heads/git-annex')
    assert_in_results(res, status='ok',
                      path=str(src.pathobj / 'test_mod_annex_file'),
                      action='copy')
    # whereis info reflects the change
    ok_(len(whereis_prior) < len(
        src.repo.whereis(files=['test_mod_annex_file'])[0]))

    # do it yet again will do nothing, because all is uptodate
    assert_status('notneeded', src.push(to='target', force=None))
    # an explicit reference point doesn't change that
    assert_status('notneeded',
                  src.push(to='target', force=None, since='HEAD~1'))

    # now force data transfer
    res = src.push(to='target', force='datatransfer')
    # no branch change, done before
    assert_in_results(res, action='publish', status='notneeded',
                      refspec='refs/heads/master:refs/heads/master')
    # no availability update
    assert_in_results(res, action='publish', status='notneeded',
                      refspec='refs/heads/git-annex:refs/heads/git-annex')
    # but data transfer
    assert_in_results(res, status='ok',
                      path=str(src.pathobj / 'test_mod_annex_file'),
                      action='copy')

    # force data transfer, but data isn't available
    src.repo.drop('test_mod_annex_file')
    res = src.push(to='target', path='.', force='datatransfer', on_failure='ignore')
    assert_in_results(res, status='impossible',
                      path=str(src.pathobj / 'test_mod_annex_file'),
                      action='copy',
                      message='Slated for transport, but no content present')


@skip_if_on_windows  # https://github.com/datalad/datalad/issues/4278
@with_tempfile(mkdir=True)
@with_tree(tree={'ria-layout-version': '1\n'})
def test_ria_push(srcpath, dstpath):
    # complex test involving a git remote, a special remote, and a
    # publication dependency
    src = Dataset(srcpath).create()
    testfile = src.pathobj / 'test_mod_annex_file'
    testfile.write_text("Heavy stuff.")
    src.save()
    assert_status(
        'ok',
        src.create_sibling_ria(
            "ria+{}".format(get_local_file_url(dstpath, compatibility='git')),
            "datastore"))
    res = src.push(to='datastore')
    assert_in_results(
        res, action='publish', target='datastore', status='ok',
        refspec='refs/heads/master:refs/heads/master')
    assert_in_results(
        res, action='publish', target='datastore', status='ok',
        refspec='refs/heads/git-annex:refs/heads/git-annex')
    assert_in_results(
        res, action='copy', target='datastore-storage', status='ok',
        path=str(testfile))


@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_gh1426(origin_path, target_path):
    # set up a pair of repos, one the published copy of the other
    origin = Dataset(origin_path).create()
    target = mk_push_target(
        origin, 'target', target_path, annex=True, bare=False)
    origin.push(to='target')
    assert_repo_status(origin.path)
    assert_repo_status(target.path)
    eq_(origin.repo.get_hexsha('master'), target.get_hexsha('master'))

    # gist of #1426 is that a newly added subdataset does not cause the
    # superdataset to get published
    origin.create('sub')
    assert_repo_status(origin.path)
    neq_(origin.repo.get_hexsha('master'), target.get_hexsha('master'))
    # now push
    res = origin.push(to='target')
    assert_result_count(
        res, 1, status='ok', type='dataset', path=origin.path,
        action='publish', target='target', operations=['fast-forward'])
    eq_(origin.repo.get_hexsha('master'), target.get_hexsha('master'))


@skip_if_on_windows  # create_sibling incompatible with win servers
@skip_ssh
@with_tree(tree={'1': '123'})
@with_tempfile(mkdir=True)
@serve_path_via_http
def test_publish_target_url(src, desttop, desturl):
    # https://github.com/datalad/datalad/issues/1762
    ds = Dataset(src).create(force=True)
    if ds.repo.is_managed_branch():
        raise SkipTest(
            'Skipped due to https://github.com/datalad/datalad/issues/4075')
    ds.save('1')
    ds.create_sibling('ssh://localhost:%s/subdir' % desttop,
                      name='target',
                      target_url=desturl + 'subdir/.git')
    results = ds.push(to='target')
    assert results
    ok_file_has_content(Path(desttop, 'subdir', '1'), '123')


@with_tempfile(mkdir=True)
@with_tempfile()
@with_tempfile()
def test_gh1763(src, target1, target2):
    # this test is very similar to test_publish_depends, but more
    # comprehensible, and directly tests issue 1763
    src = Dataset(src).create(force=True)
    target1 = mk_push_target(src, 'target1', target1, bare=False)
    target2 = mk_push_target(src, 'target2', target2, bare=False)
    src.siblings('configure', name='target2', publish_depends='target1',
                 result_renderer=None)
    # a file to annex
    (src.pathobj / 'probe1').write_text('probe1')
    src.save('probe1', to_git=False)
    # make sure the probe is annexed, not straight in Git
    assert_in('probe1', src.repo.get_annexed_files(with_content_only=True))
    # publish to target2, must handle dependency
    src.push(to='target2')
    for target in (target1, target2):
        # with a managed branch we are pushing into the corresponding branch
        # and do not see a change in the worktree
        if not target.is_managed_branch():
            # direct test for what is in the checkout
            assert_in(
                'probe1',
                target.get_annexed_files(with_content_only=True))
        # ensure git-annex knows this target has the file
        assert_in(target.config.get('annex.uuid'), src.repo.whereis(['probe1'])[0])


@with_tempfile()
@with_tempfile()
def test_gh1811(srcpath, clonepath):
    orig = Dataset(srcpath).create()
    (orig.pathobj / 'some').write_text('some')
    orig.save()
    clone = Clone.__call__(source=orig.path, path=clonepath)
    (clone.pathobj / 'somemore').write_text('somemore')
    clone.save()
    clone.repo.call_git(['checkout', 'HEAD~1'])
    res = clone.push(to='origin', on_failure='ignore')
    assert_result_count(res, 1)
    assert_result_count(
        res, 1,
        path=clone.path, type='dataset', action='publish',
        status='impossible',
        message='There is no active branch, cannot determine remote '
                'branch',
    )


@with_tempfile()
@with_tempfile()
def test_push_wanted(srcpath, dstpath):
    src = Dataset(srcpath).create()

    if src.repo.is_managed_branch():
        # on crippled FS post-update hook enabling via create-sibling doesn't
        # work ATM
        raise SkipTest("no create-sibling on crippled FS")
    (src.pathobj / 'data.0').write_text('0')
    (src.pathobj / 'secure.1').write_text('1')
    (src.pathobj / 'secure.2').write_text('2')
    src.save()

    # Dropping a file to mimic a case of simply not having it locally (thus not
    # to be "pushed")
    src.drop('secure.2', check=False)

    # Annotate sensitive content, actual value "verysecure" does not matter in
    # this example
    src.repo.set_metadata(
        add={'distribution-restrictions': 'verysecure'},
        files=['secure.1', 'secure.2'])

    src.create_sibling(
        dstpath,
        annex_wanted="not metadata=distribution-restrictions=*",
        name='target',
    )
    # check that wanted is obeyed, if instructed by configuration
    src.config.set('datalad.push.copy-auto-if-wanted', 'true', where='local')
    res = src.push(to='target')
    assert_in_results(
        res, action='copy', path=str(src.pathobj / 'data.0'), status='ok')
    for p in ('secure.1', 'secure.2'):
        assert_not_in_results(res, path=str(src.pathobj / p))
    assert_status('notneeded', src.push(to='target'))

    # check that dataset-config cannot overrule this
    src.config.set('datalad.push.copy-auto-if-wanted', 'false', where='dataset')
    res = src.push(to='target')
    assert_status('notneeded', res)

    # check the target to really make sure
    dst = Dataset(dstpath)
    # normal file, yes
    eq_((dst.pathobj / 'data.0').read_text(), '0')
    # secure file, no
    if dst.repo.is_managed_branch():
        neq_((dst.pathobj / 'secure.1').read_text(), '1')
    else:
        assert_raises(FileNotFoundError, (dst.pathobj / 'secure.1').read_text)

    # remove local config, must enable push of secure file
    src.config.unset('datalad.push.copy-auto-if-wanted', where='local')
    res = src.push(to='target')
    assert_in_results(res, path=str(src.pathobj / 'secure.1'))
    eq_((dst.pathobj / 'secure.1').read_text(), '1')
