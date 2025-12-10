# **Manage Accounts**
Here is a list of things you can do, to manage user accounts. Make sure to replace <span style="color:orange">[USERNAME]</span> accordingly. Usernames are case-sensitive.

Toggling an account is an on/off function. So use the same command to make someone admin/staff/active and to undo it.

---

## **List accounts**
List all accounts which have been created on your instance.
```
docker compose --profile production run --rm shell neodb-manage user --list
```
> **NOTE:** This shows sensitive information like the e-mail address and the fediverse account.

---

## **Create invitation code**
If you set `NEODB_INVITE_ONLY` to `true` in your .env file, only users with an invite code are able to create an account. This is how you can create a code.
```
docker compose --profile production run --rm shell neodb-manage invite --create
```


---

## **Create admin account**
A step-by-step admin account creation. If you already got an account and want to make it admin, skip to the next point.
```
docker compose --profile production run --rm shell neodb-manage createsuperuser
```

---

## **Toggle an existing account to admin**
```
docker compose --profile production run --rm shell neodb-manage user --super [USERNAME]
```
> **NOTE:** Be careful with this. An admin is able to change a lot and - possibly - mess up your instance.

---

## **Toggle an existing account to staff**
A staff account is able to manually merge entries, which already have been marked by users. There is also an option in each entry, to restrict only staff members to further edit this entry.
```
docker compose --profile production run --rm shell neodb-manage user --staff [USERNAME]
```
> **NOTE:** A user account is able to be an admin and a staff member simultaneously. An admin is not able to merge marked entries. To do so, make the admin account a staff member too.

---

## **Deactivate / activate account**
When a user misbehaves, you could deactivate the account. When deactivating an account, the e-mail is kept as "already being used", so the user is not able to create a second account with the same e-mail address. It's basically a ban.
```
docker compose --profile production run --rm shell neodb-manage user --active [USERNAME]
```

---

## **Delete account / remote identity**
Make sure that `takahe-stator` and `neodb-worker` containers are running to complete the deletion.

By username:
```
docker compose --profile production run --rm shell neodb-manage user --delete [USERNAME]
```
By remote identity:
```
docker compose --profile production run --rm shell neodb-manage user --delete [USERNAME]@remote.instance
```
> **NOTE:** You can't undo this, unless you recover a backup of your instance, which also reverts any changes that have been made since the backup.

<br>
