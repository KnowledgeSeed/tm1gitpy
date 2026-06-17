from tests.unit_common import *


class TestMDXViewCRUD:

    def test_create_mdx_view_builds_tm1py_mdxview_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()
        mdx_view = make_mdx_view(
            name="View_A",
            mdx="SELECT FROM [Cube_A]",
        )

        cube_name = "Cube_A"
        tm1py_mdxview_cls = mocker.patch("tm1_git_py.model.mdxview.TM1py.MDXView")
        tm1py_mdxview_instance = tm1py_mdxview_cls.return_value
        tm1_service.views.create.return_value = "create-result"

        result = mdxview.create_mdxview(
            tm1_service,
            mdx_view,
            uri=MDXView.uri_for("Cube_A", "View_A"),
        )

        tm1py_mdxview_cls.assert_called_once_with(
            cube_name=cube_name,
            view_name="View_A",
            MDX="SELECT FROM [Cube_A]",
        )
        tm1_service.views.create.assert_called_once_with(tm1py_mdxview_instance)
        assert result == "create-result"


    def test_delete_mdx_view_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.views.delete.return_value = "delete-result"
        mdx_view = make_mdx_view(
            name="View_A",
            mdx="SELECT FROM [Cube_A]",
        )

        result = mdxview.delete_mdxview(
            tm1_service,
            mdx_view,
            uri=MDXView.uri_for("Cube_A", "View_A"),
        )

        tm1_service.views.delete.assert_called_once_with(view_name=mdx_view.name, cube_name="Cube_A")
        assert result == "delete-result"


    def test_update_mdx_view_updates_mdx_and_calls_update(self, mocker):
        tm1_service = mocker.Mock()
        cube_name = "Cube_A"

        mdx_view_new = make_mdx_view(
            name="View_A",
            mdx="SELECT {[Dim].[Elem]} ON 0 FROM [Cube_A]",
        )

        tm1_mdx_view_obj = mocker.Mock()
        tm1_mdx_view_obj.mdx = "OLD MDX"
        tm1_service.views.get_mdx_view.return_value = tm1_mdx_view_obj
        tm1_service.views.update.return_value = "update-result"

        result = mdxview.update_mdxview(
            tm1_service,
            mdx_view_new,
            uri=MDXView.uri_for("Cube_A", "View_A"),
        )

        # Assert: we got the existing MDX view from TM1
        tm1_service.views.get_mdx_view.assert_called_once_with(
            cube_name=cube_name,
            view_name="View_A",
        )

        # The MDX on the TM1 object should be updated to the new MDX
        assert tm1_mdx_view_obj.mdx == "SELECT {[Dim].[Elem]} ON 0 FROM [Cube_A]"

        # And update() should be called with that object
        tm1_service.views.update.assert_called_once_with(tm1_mdx_view_obj)

        # Function returns whatever TM1 update() returned
        assert result == "update-result"
