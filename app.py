import streamlit as st

from pawpal_system import Owner, Pet, Priority, Task, build_plan

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

st.title("🐾 PawPal+")
st.caption("A pet-care planning assistant. Add tasks, set your time budget, and let PawPal+ build the day.")

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# --- Owner + pet info ------------------------------------------------------
st.subheader("Owner & pet")
col_o, col_p = st.columns(2)
with col_o:
    owner_name = st.text_input("Owner name", value="Jordan")
with col_p:
    pet_name = st.text_input("Pet name", value="Mochi")

col_s, col_b = st.columns(2)
with col_s:
    species = st.selectbox("Species", ["dog", "cat", "other"])
with col_b:
    breed = st.text_input("Breed (optional)", value="Shiba Inu")

st.subheader("Day constraints")
col_t, col_d, col_w = st.columns(3)
with col_t:
    available_minutes = st.number_input(
        "Time budget (min)", min_value=0, max_value=1440, value=120, step=15
    )
with col_d:
    day_start = st.text_input("Day starts at (HH:MM)", value="08:00")
with col_w:
    weekday_name = st.selectbox("Day of week", WEEKDAYS, index=0)
prefer_short_first = st.checkbox(
    "When priorities tie, do shorter tasks first", value=True
)

st.divider()

# --- Tasks -----------------------------------------------------------------
st.subheader("Tasks")
st.caption("Add care tasks. Mark a fixed time for appointments (e.g. a vet visit), or make a task weekly.")

if "tasks" not in st.session_state:
    # A couple of sensible defaults so the demo isn't empty on first load.
    st.session_state.tasks = [
        {"title": "Morning walk", "duration_minutes": 30, "priority": "high",
         "fixed_start": "", "recurrence": "daily", "weekday": "Monday"},
        {"title": "Feeding", "duration_minutes": 10, "priority": "high",
         "fixed_start": "", "recurrence": "daily", "weekday": "Monday"},
    ]

with st.form("add_task", clear_on_submit=True):
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        task_title = st.text_input("Task title", value="Enrichment play")
    with c2:
        duration = st.number_input("Duration (min)", min_value=1, max_value=240, value=20)
    with c3:
        priority = st.selectbox("Priority", ["low", "medium", "high"], index=1)

    c4, c5, c6 = st.columns(3)
    with c4:
        recurrence = st.selectbox("Recurrence", ["daily", "weekly"])
    with c5:
        recur_weekday = st.selectbox("Weekly on", WEEKDAYS, index=0)
    with c6:
        fixed_start = st.text_input("Fixed time (HH:MM, optional)", value="")

    if st.form_submit_button("Add task"):
        st.session_state.tasks.append(
            {
                "title": task_title,
                "duration_minutes": int(duration),
                "priority": priority,
                "fixed_start": fixed_start.strip(),
                "recurrence": recurrence,
                "weekday": recur_weekday,
            }
        )

if st.session_state.tasks:
    st.write("Current tasks:")
    st.table(st.session_state.tasks)
    if st.button("Clear all tasks"):
        st.session_state.tasks = []
        st.rerun()
else:
    st.info("No tasks yet. Add one above.")

st.divider()

# --- Build schedule --------------------------------------------------------
st.subheader("Build schedule")

if st.button("Generate schedule", type="primary"):
    try:
        owner = Owner(
            name=owner_name,
            available_minutes=int(available_minutes),
            day_start=day_start.strip() or "08:00",
            prefer_short_first=prefer_short_first,
        )
        pet = Pet(name=pet_name, species=species, breed=breed.strip() or None)

        tasks = []
        for row in st.session_state.tasks:
            tasks.append(
                Task(
                    title=row["title"],
                    duration_minutes=int(row["duration_minutes"]),
                    priority=row["priority"],
                    fixed_start=row.get("fixed_start") or None,
                    recurrence=row.get("recurrence", "daily"),
                    weekday=(
                        WEEKDAYS.index(row.get("weekday", "Monday"))
                        if row.get("recurrence") == "weekly"
                        else None
                    ),
                )
            )

        weekday = WEEKDAYS.index(weekday_name)
        plan = build_plan(owner, pet, tasks, weekday=weekday)
    except ValueError as exc:
        st.error(f"Couldn't build the plan: {exc}")
    else:
        st.success(f"Daily plan for {pet.display} — {weekday_name}")

        if plan.scheduled:
            st.markdown("#### 📅 Plan")
            for item in plan.scheduled:
                st.markdown(
                    f"**{item.start}–{item.end}** — {item.task.title} "
                    f"*({item.task.duration_minutes} min, "
                    f"{item.task.priority.label} priority)*  \n"
                    f"<span style='color:gray'>↳ {item.reason}</span>",
                    unsafe_allow_html=True,
                )
        else:
            st.warning("No tasks could be scheduled with these constraints.")

        col_used, col_free = st.columns(2)
        col_used.metric("Time used", f"{plan.total_scheduled_minutes} min")
        col_free.metric("Time free", f"{plan.remaining_minutes} min")

        if plan.skipped:
            st.markdown("#### ⏭️ Skipped")
            for skip in plan.skipped:
                st.markdown(f"- **{skip.task.title}** — {skip.reason}")

        with st.expander("Plain-text plan (copy for your README)"):
            st.code(plan.render(), language="text")
