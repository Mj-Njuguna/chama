# Chama

Bookkeeping and governance for Kenyan chamas, welfare groups and table-banking groups.

Your group's money stays in your group's own M-Pesa paybill or bank account. Chama keeps the books:
contributions, fines, table-banking loans, expenses, member statements and year-end shareout.

## Features

- Sign in with your phone number
- Run several chamas from one account, with roles for chair, treasurer, secretary and members
- Set your group's rules: contribution amount and schedule, grace period, fines, loan limits
- Contribution reminders and automatic payment recording from M-Pesa
- Table-banking loans with guarantors, official approvals and repayment schedules
- Expenses approved by two officials
- Member statements and a year-end shareout calculator

## Status

Early development. Not ready for production use.

## Project layout

```
backend/   API and background jobs (Django)
mobile/    Android app (React Native + Expo)
```

## Run it locally

You need Docker Desktop, [`just`](https://github.com/casey/just) and Node.js 22 or newer.

```sh
cd backend
just up          # start the API and its services on http://localhost:8001
just test        # run the backend tests
```

```sh
cd mobile
npm ci
npx expo start   # open the app on an Android emulator or device
```

Copy `backend/.env.example` to `backend/.env` to change ports or settings.

## License

[AGPL-3.0](LICENSE)
