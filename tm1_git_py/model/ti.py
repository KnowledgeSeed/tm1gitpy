import os


class TI:
    def __init__(self, prolog_procedure, metadata_procedure, data_procedure, epilog_procedure):
        self.prolog_procedure = prolog_procedure
        self.metadata_procedure = metadata_procedure
        self.data_procedure = data_procedure
        self.epilog_procedure = epilog_procedure

    @classmethod
    def get_string_between(cls, text: str, start_keyword, end_keyword):
        start_idx = text.find(start_keyword)
        end_idx = text.find(end_keyword, start_idx)

        if start_idx != -1 and end_idx != -1:
            start_idx += len(start_keyword)
            result = text[start_idx:end_idx]
            return result
        else:
            print("Keywords not found")

    def __eq__(self, other):
        if not isinstance(other, TI):
            return NotImplemented

        if _normalize_line_sep(self.prolog_procedure) != _normalize_line_sep(other.prolog_procedure):
            return False

        if _normalize_line_sep(self.metadata_procedure) != _normalize_line_sep(other.metadata_procedure):
            return False

        if _normalize_line_sep(self.data_procedure) != _normalize_line_sep(other.data_procedure):
            return False

        if _normalize_line_sep(self.epilog_procedure) != _normalize_line_sep(other.epilog_procedure):
            return False

        return True

    def __hash__(self):
        return hash(tuple(sorted(self.to_dict().items())))

    def to_dict(self):
        return {
            'prolog_procedure': self.prolog_procedure,
            'metadata_procedure': self.metadata_procedure,
            'data_procedure': self.data_procedure,
            'epilog_procedure': self.epilog_procedure,
        }

    @classmethod
    def from_string(cls, ti):
        return TI(
            cls.get_string_between(ti, '#region Prolog', '#endregion').strip(),
            cls.get_string_between(
                ti, '#region Metadata', '#endregion').strip(),
            cls.get_string_between(ti, '#region Data', '#endregion').strip(),
            cls.get_string_between(ti, '#region Epilog', '#endregion').strip())

    def ti_as_string(self):
        line_sep = os.linesep
        if line_sep is None:
            line_sep = "\r\n"
        sections = [
            "#region Prolog",
            self.prolog_procedure,
            "#endregion",
            "#region Metadata",
            self.metadata_procedure,
            "#endregion",
            "#region Data",
            self.data_procedure,
            "#endregion",
            "#region Epilog",
            self.epilog_procedure,
            "#endregion"
        ]
        return line_sep.join(sections) + line_sep


def _normalize_line_sep(ti_attribute: str) -> str:
    return ti_attribute.replace('\r', '').strip()
