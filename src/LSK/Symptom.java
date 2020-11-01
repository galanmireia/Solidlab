package LSK;

public class Symptom {
	public String name;
	int covidImpact;
	int severityIndex;
	
	public Symptom(int covidImpact, int severityIndex, String name) {
		this.covidImpact = covidImpact;
		this.severityIndex = severityIndex;
		this.name = name;
	}

	public void show() {
		System.out.println("symptom value :" + name + " severityIndex :" + severityIndex + " covidImpact:" + covidImpact);
	}


}