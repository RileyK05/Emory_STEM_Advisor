# How to contribute (step by step)

You **cannot push to this repo directly**. That's on purpose. Everything goes
through this loop:

```
your fork  ──►  pull request  ──►  Riley reviews  ──►  merged into main
```

Follow the steps in order. Copy the commands exactly. Anything in `<angle
brackets>` is something you replace (drop the brackets too).

Commands work in **Git Bash** (Windows), Terminal (Mac), or any Linux shell.
If you don't have git: install it from https://git-scm.com/downloads and
restart your terminal.

---

## 0. One-time setup: tell git who you are

```bash
git config --global user.name "<Your Name>"
git config --global user.email "<the email on your GitHub account>"
```

---

## 1. One-time setup: create your fork

A **fork** is your own copy of this repo on GitHub. You can do whatever you
want in it.

1. Go to https://github.com/RileyK05/Emory_STEM_Advisor
2. Click **Fork** (top right) → **Create fork**.
3. You now have `https://github.com/<your-username>/Emory_STEM_Advisor`.

## 2. One-time setup: download your fork to your computer

```bash
git clone https://github.com/<your-username>/Emory_STEM_Advisor.git
cd Emory_STEM_Advisor
```

Now link it to the original repo so you can pull in updates. The original is
called `upstream`; your fork is called `origin`.

```bash
git remote add upstream https://github.com/RileyK05/Emory_STEM_Advisor.git
git remote -v
```

You should see four lines: two `origin` (your fork) and two `upstream`
(Riley's). If yes, setup is done forever.

---

## 3. Every time you start new work

**Step 1: get the latest main.** Always do this first.

```bash
git checkout main
git fetch upstream
git merge upstream/main
git push origin main
```

**Step 2: make a new branch for this piece of work.** Name it after what
you're doing, e.g. `fix-login-button`.

```bash
git checkout -b <branch-name>
```

**Rule: never commit directly on `main`, not even in your fork.** Your
`main` should always be an exact copy of Riley's `main`.

## 4. Save your work (commit)

Make your changes, then:

```bash
git status                  # see what changed
git add <file1> <file2>     # or: git add .   (adds everything)
git commit -m "<Short description of what you changed>"
```

Commit messages should say what changed, e.g. "Fix Python version pin for
tokenizers". Treat them as permanent and public, because they are.

Commit as often as you like.

## 5. Upload your work to your fork

```bash
git push -u origin <branch-name>
```

The first time on a branch you need `-u origin <branch-name>`. After that,
plain `git push` works.

## 6. Open a pull request

1. Go to your fork on GitHub. A yellow bar should say **"Compare & pull
   request"**. Click it.
   (No bar? Go to the **Pull requests** tab → **New pull request**.)
2. Check the top of the page reads:
   **base repository: `RileyK05/Emory_STEM_Advisor`, base: `main`**
   ← **head repository: `<you>/Emory_STEM_Advisor`, compare: `<branch-name>`**
3. Write a title and fill in the description.
4. Click **Create pull request**.

That's it. Riley reviews it and either merges it or leaves comments.

## 7. Riley asked for changes on my PR

Just make the changes on the **same branch**, commit, and push:

```bash
git checkout <branch-name>
# ...edit files...
git add .
git commit -m "<What you fixed>"
git push
```

The pull request updates automatically. Don't open a new one.

## 8. My PR says it has conflicts / main moved on while I was working

Bring the latest main into your branch:

```bash
git checkout main
git fetch upstream
git merge upstream/main
git push origin main

git checkout <branch-name>
git merge main
```

- **No conflicts:** run `git push`. Done.
- **Conflicts:** git lists the conflicted files. Open each one and look for:

  ```
  <<<<<<< HEAD
  your version
  =======
  the version from main
  >>>>>>> main
  ```

  Edit it into what the file *should* say, and delete the `<<<<<<<`,
  `=======`, `>>>>>>>` lines. Then:

  ```bash
  git add <file>
  git commit -m "Merge main into <branch-name>"
  git push
  ```

  Not sure which version is right? **Stop and ask Riley.** Don't guess.

## 9. After your PR is merged

Clean up and go back to step 3 for the next thing:

```bash
git checkout main
git fetch upstream
git merge upstream/main
git push origin main
git branch -d <branch-name>
```

---

## Never do these

| Don't | Why |
|---|---|
| `git push --force` / `git push -f` | Deletes history. Ask Riley instead. |
| `git reset --hard` (unless Riley said to) | Throws away your uncommitted work permanently. |
| Commit on `main` | Your `main` must mirror Riley's. Always use a branch. |
| Commit secrets, API keys, `.env` files, or big data files | Anything pushed is public and effectively permanent. |
| Put one giant PR together for many unrelated things | Small PRs get reviewed fast; big ones sit. |

## Oh no, what do I do

| Situation | Fix |
|---|---|
| I committed on `main` by accident (not pushed yet) | `git branch <new-branch-name>` then `git reset --hard upstream/main` then `git checkout <new-branch-name>`. Your commit is now on the new branch. |
| I'm on the wrong branch but haven't committed | `git stash`, `git checkout <right-branch>`, `git stash pop` |
| `git push` says "rejected" / "non-fast-forward" | Run `git pull`, then `git push` again. If `pull` shows conflicts, see step 8. |
| `git push` says permission denied to `RileyK05/...` | You're pushing to Riley's repo, not your fork. Run `git remote -v`: `origin` must be *your* username. |
| Something weird and I'm scared | Stop. Don't run more commands. Copy the output of `git status` and send it to Riley. |
