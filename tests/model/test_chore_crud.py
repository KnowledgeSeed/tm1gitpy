from tests.unit_common import *


class TestChoreCRUD:

    def test_create_chore_builds_tm1py_chore_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()

        chore_mock = make_chore(
            name="Chore_A",
            start_time="2025-04-22T10:07:00+01:00",
            dst_sensitive=True,
            active=False,
            execution_mode="SingleCommit",
            frequency="P01DT00H00M00S",
            task_names=["Proc1", "Proc2"],
        )

        create_chore_task_mock = mocker.patch(
            "tm1_git_py.model.chore.create_chore_task"
        )
        chore_task_instances = [
            mocker.Mock(name="ChoreTask0"),
            mocker.Mock(name="ChoreTask1"),
        ]
        create_chore_task_mock.side_effect = chore_task_instances

        start_time_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreStartTime.from_string",
            return_value="parsed-start-time",
        )
        frequency_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreFrequency.from_string",
            return_value="parsed-frequency",
        )

        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value
        tm1_service.chores.create.return_value = "create-result"

        result = chore.create_chore(tm1_service, chore_mock)

        assert create_chore_task_mock.call_count == 2
        create_chore_task_mock.assert_any_call(task=chore_mock.tasks[0], step=0)
        create_chore_task_mock.assert_any_call(task=chore_mock.tasks[1], step=1)

        start_time_from_string.assert_called_once_with(chore_mock.start_time)
        frequency_from_string.assert_called_once_with(chore_mock.frequency)

        tm1py_chore_cls.assert_called_once_with(
            name="Chore_A",
            start_time="parsed-start-time",
            dst_sensitivity=True,
            active=False,
            execution_mode="SingleCommit",
            frequency="parsed-frequency",
            tasks=chore_task_instances,
        )

        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"


    def test_delete_chore_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.chores.delete.return_value = "delete-result"
        chore_obj = make_chore(name="Chore_To_Delete")

        result = chore.delete_chore(tm1_service, chore_obj)

        tm1_service.chores.delete.assert_called_once_with("Chore_To_Delete")
        assert result == "delete-result"


    def test_update_chore_updates_fields_and_tasks_when_exists(self, mocker):
        tm1_service = mocker.Mock()

        chore_new = make_chore(
            name="Chore_A",
            start_time="2025-04-23T10:00:00+01:00",
            dst_sensitive=False,
            active=True,
            execution_mode="MultipleCommit",
            frequency="P02DT00H00M00S",
            task_names=["Proc1_new", "Proc2_new"],
        )

        tm1_service.chores.create.return_value = "create-result"
        create_chore_task_mock = mocker.patch(
            "tm1_git_py.model.chore.create_chore_task"
        )
        chore_task_instances = [
            mocker.Mock(name="ChoreTask0_new"),
            mocker.Mock(name="ChoreTask1_new"),
        ]
        create_chore_task_mock.side_effect = chore_task_instances

        start_time_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreStartTime.from_string",
            return_value="parsed-start-time",
        )
        frequency_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreFrequency.from_string",
            return_value="parsed-frequency",
        )
        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value

        result = chore.update_chore(tm1_service, chore_new)

        assert create_chore_task_mock.call_count == 2
        create_chore_task_mock.assert_any_call(task=chore_new.tasks[0], step=0)
        create_chore_task_mock.assert_any_call(task=chore_new.tasks[1], step=1)

        start_time_from_string.assert_called_once_with(chore_new.start_time)
        frequency_from_string.assert_called_once_with(chore_new.frequency)
        tm1_service.chores.delete.assert_called_once_with("Chore_A")
        tm1py_chore_cls.assert_called_once_with(
            name="Chore_A",
            start_time="parsed-start-time",
            dst_sensitivity=False,
            active=True,
            execution_mode="MultipleCommit",
            frequency="parsed-frequency",
            tasks=chore_task_instances,
        )
        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"


    def test_update_chore_activates_when_active_flag_changes_from_false_to_true(self, mocker):
        tm1_service = mocker.Mock()

        chore_new = make_chore(
            name="Chore_B",
            active=True,
            task_names=["ProcX"],
        )

        tm1_service.chores.create.return_value = "create-result"

        mocker.patch("tm1_git_py.model.chore.create_chore_task", return_value=mocker.Mock())
        mocker.patch("tm1_git_py.model.chore.ChoreStartTime.from_string", return_value="parsed-start-time")
        mocker.patch("tm1_git_py.model.chore.ChoreFrequency.from_string", return_value="parsed-frequency")
        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value

        result = chore.update_chore(tm1_service, chore_new)

        tm1_service.chores.delete.assert_called_once_with("Chore_B")
        tm1py_chore_cls.assert_called_once()
        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"

    def test_update_chore_accepts_date_only_start_time(self, mocker):
        tm1_service = mocker.Mock()

        chore_new = make_chore(
            name="Chore_DateOnly",
            start_time="2026-03-05",
            task_names=["ProcX"],
        )

        tm1_service.chores.create.return_value = "create-result"

        mocker.patch("tm1_git_py.model.chore.create_chore_task", return_value=mocker.Mock())
        start_time_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreStartTime.from_string",
            return_value="parsed-start-time"
        )
        mocker.patch(
            "tm1_git_py.model.chore.ChoreFrequency.from_string",
            return_value="parsed-frequency"
        )
        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value

        result = chore.update_chore(tm1_service, chore_new)

        start_time_from_string.assert_called_once_with("2026-03-05T00:00:00+00:00")
        tm1_service.chores.delete.assert_called_once_with("Chore_DateOnly")
        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"
